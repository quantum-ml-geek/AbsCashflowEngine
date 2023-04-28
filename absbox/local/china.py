import logging, os, re, itertools
import requests, shutil, json
from dataclasses import dataclass,field
import functools, pickle, collections
import pandas as pd
import numpy as np
from urllib.request import unquote
from functools import reduce 
from pyspecter import query

from absbox import *
from absbox.local.util import mkTag,DC,mkTs,consolStmtByDate,aggStmtByDate,subMap,subMap2,mapValsBy,mapListValBy,renameKs2
from absbox.local.component import *


@dataclass
class SPV:
    名称: str
    日期: dict
    资产池: dict
    账户: tuple
    债券: tuple
    费用: tuple
    分配规则: dict
    归集规则: tuple
    流动性支持:dict = None
    利率对冲:dict = None
    汇率对冲:dict = None
    触发事件: dict = None
    状态:str = "摊销"
    自定义: dict = None

    @classmethod
    def load(cls,p):
        with open(p,'rb') as _f:
            c = _f.read()
        return pickle.loads(c)

    @classmethod
    def pull(cls,_id,p,url=None,pw=None):
        def get_filename_from_cd(cd):
            if not cd:
                return None
            fname = re.findall("filename\*=utf-8''(.+)", cd)
            if len(fname) == 0:
                fname1 = re.findall("filename=\"(.+)\"", cd)
                return fname1[0]
            return unquote(fname[0])
        with requests.get(f"{url}/china/deal/{_id}",stream=True,verify=False) as r:
            filename = get_filename_from_cd(r.headers.get('content-disposition'))
            if filename is None:
                logging.error("Can't not find the Deal Name")
                return None
            with open(os.path.join(p,filename),'wb') as f:
                shutil.copyfileobj(r.raw, f)
            logging.info(f"Download {p} {filename} done ")


    @property
    def json(self):
        stated = False 
        dists,collects,cleans = [ self.分配规则.get(wn,[]) for wn in ['未违约','回款后','清仓回购'] ]
        distsAs,collectsAs,cleansAs = [ [ mkWaterfall2(_action) for _action in _actions] for _actions in [dists,collects,cleans] ]
        distsflt,collectsflt,cleanflt = [ itertools.chain.from_iterable(x) for x in [distsAs,collectsAs,cleansAs] ]
        parsedDates = mkDate(self.日期)
        defaultStartDate = self.日期.get("起息日",None) or self.日期['归集日'][0]
        """
        get the json formatted string
        """
        _r = {
            "dates": parsedDates,
            "name": self.名称,
            "status": mkStatus(self.状态),
            "pool":{"assets": [mkAsset(x) for x in self.资产池.get('清单',[])]
                , "asOfDate": self.日期.get('封包日',None) or self.日期['归集日'][0]
                , "issuanceStat": readIssuance(self.资产池)
                , "futureCf":mkCf(self.资产池.get('归集表', []))},
            "bonds": {bn: mkBnd(bn,bo)  for (bn,bo) in self.债券 },
            "waterfall": mkWaterfall({},self.分配规则.copy()),
            "fees": {fn :mkFee(fo|{"名称":fn},fsDate=defaultStartDate) for (fn,fo) in self.费用 },
            "accounts": {an:mkAcc(an,ao) for (an,ao) in self.账户 },
            "collects": [ mkCollection(c) for c in self.归集规则],
            "rateSwap": {k:mkRateSwap(v) for k,v in self.利率对冲.items()} if self.利率对冲 else None,
            "currencySwap": None,
            "custom": {cn:mkCustom(co) for cn,co in self.自定义.items()} if self.自定义 else None,
            "triggers": renameKs2(mapListValBy(self.触发事件,mkTrigger),chinaDealCycle) if self.触发事件 else None,
            "liqProvider": {ln: mkLiqProvider(ln, lo | {"起始日":defaultStartDate} ) 
                                for ln,lo in self.流动性支持.items() } if self.流动性支持 else None
        }
        
        _dealType = identify_deal_type(_r)

        return mkTag((_dealType,_r))

    def _get_bond(self, bn):
        for _bn,_bo in self.债券:
            if _bn == bn:
                return _bo
        return None
   
    def read_assump(self, assump):
        if assump:
            return [mkAssumption(a) for a in assump]
        return None

    def read_pricing(self, pricing):
        if pricing:
            return mkPricingAssump(pricing)
        return None

    def read(self, resp, position=None):
        read_paths = {'bonds': ('bndStmt', china_bondflow_fields, "债券")
                    , 'fees': ('feeStmt', china_fee_flow_fields_d, "费用")
                    , 'accounts': ('accStmt', china_acc_flow_fields_d , "账户")
                    , 'liqProvider': ('liqStmt', china_liq_flow_fields_d, "流动性支持")
                    , 'rateSwap': ('rsStmt', china_rs_flow_fields_d, "")
                    }
        deal_content = resp[0]['contents']
        output = {}
        for comp_name, comp_v in read_paths.items():
            if (not comp_name in deal_content) or (deal_content[comp_name] is None):
                continue
            output[comp_name] = {}
            for k, x in deal_content[comp_name].items():
                ir = None
                if x[comp_v[0]]:
                    ir = [_['contents'] for _ in x[comp_v[0]]]
                output[comp_name][k] = pd.DataFrame(ir, columns=comp_v[1]).set_index("日期")
            output[comp_name] = collections.OrderedDict(sorted(output[comp_name].items()))
        # aggregate fees
        output['fees'] = {f: v.groupby('日期').agg({"余额": "min", "支付": "sum", "剩余支付": "min"})
                          for f, v in output['fees'].items()}

        # aggregate accounts
        output['agg_accounts'] = aggAccs(output['accounts'],'cn')

        output['pool'] = {}
        _pool_cf_header,_ = guess_pool_flow_header(deal_content['pool']['futureCf'][0],"chinese")
        output['pool']['flow'] = pd.DataFrame([_['contents'] for _ in deal_content['pool']['futureCf']]
                                              , columns=_pool_cf_header)
        pool_idx = "日期"
        output['pool']['flow'] = output['pool']['flow'].set_index(pool_idx)
        output['pool']['flow'].index.rename(pool_idx, inplace=True)

        output['pricing'] = readPricingResult(resp[3], 'cn')
        if position:
            output['position'] = {}
            for k,v in position.items():
                if k in output['bonds']:
                    b = self._get_bond(k)
                    factor = v / b["初始余额"] / 100
                    if factor > 1.0:
                        raise  RuntimeError("持仓系数大于1.0")
                    output['position'][k] = output['bonds'][k][china_bond_cashflow].apply(lambda x:x*factor).round(4)

        output['result'] = readRunSummary(resp[2], 'cn')
        return output



信贷ABS = SPV # Legacy ,to be deleted