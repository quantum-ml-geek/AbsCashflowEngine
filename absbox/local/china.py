import logging, os, re, itertools
import requests, shutil
from dataclasses import dataclass,field
import functools, pickle, collections
import pandas as pd
import numpy as np
from urllib.request import unquote
from enum import Enum
from functools import reduce 
import matplotlib.pyplot as plt

from absbox import *
from absbox.local.util import mkTag,DC,mkTs,query,consolStmtByDate,aggStmtByDate

from absbox.local.component import *

class 频率(Enum):
    每月 = 12
    每季度 = 4
    每半年 = 2
    每年 = 1


freqMap = {"每月": "Monthly"
    , "每季度": "Quarterly"
    , "每半年": "SemiAnnually"
    , "每年": "Annually"}

baseMap = {"资产池余额": "CurrentPoolBalance"
           , "资产池期末余额": "CurrentPoolBalance"
           , "资产池期初余额": "CurrentPoolBegBalance"
           , "资产池初始余额": "OriginalPoolBalance"
           , "资产池当期利息":"PoolCollectionInt"
           , "债券余额":"CurrentBondBalance"
           , "债券初始余额":"OriginalBondBalance"
           , "当期已付债券利息":"LastBondIntPaid"
           , "当期已付费用" :"LastFeePaid"
           , "当期未付债券利息" :"CurrentDueBondInt"
           , "当期未付费用": "CurrentDueFee"
           }


def mkDateVector(x):
    match x:
        case dp if isinstance(dp,str):
            return mkTag(datePattern[dp])
        case [dp, *p] if (dp in datePattern.keys()):
            return mkTag((datePattern[dp],p))
        case _ :
            raise RuntimeError(f"not match found: {x}")

def mkBondType(x):
    match x:
        case {"固定摊还": schedule}:
            return mkTag(("PAC", mkTag(("BalanceCurve", schedule))))
        case {"过手摊还": None}:
            return mkTag(("Sequential"))
        case {"锁定摊还": _after}:
            return mkTag(("Lockout", _after))
        case {"权益": _}:
            return mkTag(("Equity"))

def mkAccType(x):
    match x:
        case {"固定储备金额": amt}:
            return mkTag(("FixReserve", amt))
        case {"目标储备金额": [base, rate]}:
            match base:
                case ["合计",*q]:
                    return mkTag(("PctReserve"
                                 , [mkTag(("Sum"
                                           ,[mkTag((baseMap[_b], _ts)) for (_b, _ts) in q]))
                                   , rate ]))
                case _ :
                    return mkTag(("PctReserve", [mkTag((baseMap[base])), rate]))
        case {"目标储备金额": {"公式":ds,"系数":rate}}:
            return mkTag(("PctReserve",[mkDs(ds), rate]))
        case {"目标储备金额": {"公式":ds}}:
            return mkTag(("PctReserve",[mkDs(ds), 1.0]))
        case {"较高": [a, b]}:
            return mkTag(("Max", [mkAccType(a), mkAccType(b)]))
        case {"较低": [a, b]}:
            return mkTag(("Min", [mkAccType(a), mkAccType(b)]))
        case {"分段": [p,a,b]}:
            return mkTag(("Either", [mkPre(p) ,mkAccType(a), mkAccType(b)]))


def mkAccInt(x):
    match x:
        case {"周期": _dp, "利率": br, "最近结息日": lsd}:
            return [br, lsd, mkDateVector(_dp)]
        case _:
            return None


def mkFeeType(x):
    match x:
        case {"年化费率": [base, rate]}:
            return mkTag(("AnnualRateFee"
                        , [ mkTag((baseMap[base],'1970-01-01')) 
                           , rate]))
        case {"百分比费率": [*desc, rate]}:
            match desc:
                case ["资产池回款","利息"]:
                    return mkTag(("PctFee", [mkTag(("PoolCollectionIncome", "CollectedInterest")), rate]))
                case ["已付利息合计", *bns]:
                    return mkTag(("PctFee", [mkTag(("LastBondIntPaid",bns)), rate]))
                case ["已付本金合计", *bns]:
                    return mkTag(("PctFee", [mkTag(("LastBondPrinPaid",bns)), rate]))
                case _:
                    raise RuntimeError(f"Failed to match on 百分比费率：{desc,rate}")
        case {"固定费用": amt}:
            return mkTag(("FixFee", amt))
        case {"周期费用": [p, amt]}:
            return mkTag(("RecurFee", [mkDatePattern(p), amt]))


def mkRateReset(x):
    match x:
        case {"重置期间": interval, "起始": sdate}:
            return mkTag(("ByInterval", [freqMap[interval], sdate]))
        case {"重置期间": interval}:
            return mkTag(("ByInterval", [freqMap[interval], None]))
        case {"重置月份": monthOfYear}:
            return mkTag(("MonthOfYear", monthOfYear))


def mkBondRate(x):
    indexMapping = {"LPR5Y": "LPR5Y", "LIBOR1M": "LIBOR1M"}
    match x:
        case {"浮动": [_index, Spread, resetInterval],"日历":dc}:
            return {"tag": "Floater"
                , "contents": [indexMapping[_index]
                    , Spread
                    , mkRateReset(resetInterval)
                    , dc
                    , None
                    , None]}
        case {"浮动": [_index, Spread, resetInterval]}:
            x['日历']=DC.DC_ACT_365F.value
            return mkBondRate(x)
        case {"固定": _rate, "日历":dc}:
            return mkTag(("Fix",[_rate,dc]))
        case {"固定": _rate}:
            return mkTag(("Fix",[_rate,DC.DC_ACT_365F.value]))
        case {"期间收益": _yield}:
            return mkTag(("InterestByYield",_yield))


def mkFeeCapType(x):
    match x:
        case {"应计费用百分比": pct}:
            return mkTag(("DuePct",pct))
        case {"应计费用上限": amt}:
            return mkTag(("DueCapAmt",amt))

def mkPDA(x):
    match x:
        case {"公式": ds}:
            return mkTag(("DS",mkDs(ds)))

def mkAccountCapType(x):
    match x:
        case {"余额百分比": pct}:
            return mkTag(("DuePct",pct))
        case {"金额上限": amt}:
            return mkTag(("DueCapAmt",amt))

def mkTransferLimit(x):
    match x:
        case {"余额百分比": pct}:
            return mkTag(("DuePct",pct))
        case {"金额上限": amt}:
            return mkTag(("DueCapAmt",amt))
        case {"公式": "ABCD" }:
            return mkTag(("Formula","ABCD"))
        case {"公式": formula}:
            return mkTag(("DS",mkDs(formula)))


def mkLiqMethod(x):
    match x:
        case ["正常|违约",a,b]:
            return mkTag(("BalanceFactor",[a,b]))
        case ["正常|拖欠|违约",a,b,c]:
            return mkTag(("BalanceFactor2",[a,b,c]))
        case ["贴现|违约",a,b]:
            return mkTag(("PV",[a,b]))

def mkAction(x):
    match x:
        case ["账户转移", source, target]:
            return mkTag(("Transfer",[source, target]))
        case ["按公式账户转移", _limit, source, target]:
            return mkTag(("TransferBy",[mkTransferLimit(_limit), source, target]))
        case ["计提费用", *feeNames]:
            return mkTag(("CalcFee",feeNames))
        case ["计提利息", *bndNames]:
            return mkTag(("CalcBondInt",bndNames))
        case ["支付费用", source, target]:
            return mkTag(("PayFee",[source, target]))
        case ["支付费用收益", source, target, _limit]:
            limit = mkAccountCapType(_limit)
            return mkTag(("PayFeeResidual",[limit, source, target]))
        case ["支付费用收益", source, target]:
            return mkTag(("PayFeeResidual",[None, source, target]))
        case ["支付费用限额", source, target, _limit]:
            limit = mkFeeCapType(_limit)
            return mkTag(("PayFeeBy",[limit, source, target]))
        case ["支付利息", source, target]:
            return mkTag(("PayInt",[source, target]))
        case ["支付本金", source, target, _limit]:
            pda = mkPDA(_limit)
            return mkTag(("PayPrinBy",[pda, source, target]))
        case ["支付本金", source, target]:
            return mkTag(("PayPrin",[source, target]))
        case ["支付剩余本金", source, target]:
            return mkTag(("PayPrinResidual",[source, target]))
        case ["支付期间收益", source, target]:
            return mkTag(("PayTillYield",[source, target]))
        case ["支付收益", source, target, limit]:
            return mkTag(("PayResidual",[limit, source, target]))
        case ["支付收益", source, target]:
            return mkTag(("PayResidual",[None, source, target]))
        case ["储备账户转移", source, target, satisfy]:
            _map = {"源储备":"Source", "目标储备": "Target"}
            return mkTag(("TransferReserve",[_map[satisfy], source, target]))
        case ["出售资产", liq, target]:
            return mkTag(("LiquidatePool",[mkLiqMethod(liq), target]))
        case ["流动性支持",source, target, limit]:
            return mkTag(("LiqSupport",[ mkTag(("DS",mkDs(limit))), source, target]))
        case ["流动性支持",source, target]:
            return mkTag(("LiqSupport",[None, source, target]))
        case ["流动性支持偿还",source, target]:
            return mkTag(("LiqRepay",[None, source, target]))
        case ["流动性支持报酬",source, target]:
            return mkTag(("LiqYield",[None, source, target]))

#data DealStats =
#              | CurrentPoolDefaultedBalance
#              | PoolCollectionInt  -- a redirect map to `CurrentPoolCollectionInt T.Day`
#              | FutureOriginalPoolBalance
#              | CurrentDueBondInt [String]
#              | CurrentDueFee [String]
#              | LastBondIntPaid [String]
#              | LastFeePaid [String]


def mkDs(x):
    "Making Deal Stats"
    match x:
        case ("债券余额",):
            return mkTag("CurrentBondBalance")
        case ("资产池余额",):
            return mkTag("CurrentPoolBalance")
        case ("初始债券余额",):
            return mkTag("OriginalBondBalance")
        case ("初始资产池余额",):
            return mkTag("OriginalPoolBalance")
        case ("债券系数",):
            return mkTag("BondFactor")
        case ("资产池系数",):
            return mkTag("PoolFactor")
        case ("所有账户余额",):
            return mkTag("AllAccBalance")
        case ("账户余额",*ans):
            return mkTag(("AccBalance",ans))
        case ("系数",ds,f):
            return mkTag(("Factor",[mkDs(ds),f]))
        case ("债券余额",*bnds):
            return mkTag(("CurrentBondBalanceOf",bnds))
        case ("债券待付利息",*bnds):
            return mkTag(("CurrentDueBondInt",bnds))
        case ("待付费用",*fns):
            return mkTag(("CurrentDueFee",fns))
        case ("Min",ds1,ds2):
            return mkTag(("Min",[mkDs(ds1),mkDs(ds2)]))
        case ("Max",ds1,ds2):
            return mkTag(("Max",[mkDs(ds1),mkDs(ds2)]))
        case ("合计",*ds):
            return mkTag(("Sum",[mkDs(_ds) for _ds in ds]))
        case ("差额",*ds):
            return mkTag(("Substract",[mkDs(_ds) for _ds in ds]))
        case ("常数",n):
            return mkTag(("Constant",n))
        case ("储备账户缺口",*accs):
            return mkTag(("ReserveAccGap",accs))
        case ("自定义",n):
            return mkTag(("UseCustomData",n))
            


def mkPre(p):
    dealStatusMap = {"摊还":"Current"
                     ,"加速清偿":"Accelerated"
                     ,"循环":"Revolving"}
    match p:
        case [ds,">",amt]:
            return mkTag(("IfGT",[mkDs(ds),amt]))
        case [ds,"<",amt]:
            return mkTag(("IfLT",[mkDs(ds),amt]))
        case [ds,">=",amt]:
            return mkTag(("IfGET",[mkDs(ds),amt]))
        case [ds,"<=",amt]:
            return mkTag(("IfLET",[mkDs(ds),amt]))
        case [ds,"=",0]:
            return mkTag(("IfZero",mkDs(ds)))
        case [">",_d]:
            return mkTag(("IfAfterDate",_d))
        case ["<",_d]:
            return mkTag(("IfBeforeDate",_d))
        case [">=",_d]:
            return mkTag(("IfAfterOnDate",_d))
        case ["<=",_d]:
            return mkTag(("IfBeforeOnDate",_d))
        case ["状态",_ds]:
            return mkTag(("IfDealStatus",dealStatusMap[_ds]))
        case ["同时满足",_p1,_p2]:
            return mkTag(("And",mkPre(_p1),mkPre(_p2)))
        case ["任一满足",_p1,_p2]:
            return mkTag(("Or",mkPre(_p1),mkPre(_p2)))

def isPre(x):
    return mkPre(x) is not None

def mkWaterfall(x):
    match x:
        case (pre,_action): 
            action = mkAction(_action)
            return [mkPre(pre),action]
        case _:
            return [None,mkAction(x)]

def mkWaterfall2(x):
    match x:
        case (pre, *_action) if isPre(pre) and len(x)>2: # pre with multiple actions
            _pre = mkPre(pre)
            return [[ _pre, mkAction(a) ] for a in _action ]
        case (pre, _action) if isPre(pre) and len(x)==2: # pre with multiple actions
            _pre = mkPre(pre)
            return [[ _pre, mkAction(_action) ]]
        case _:
            return [[ None,mkAction(x) ]]


def mkAssetRate(x):
    match x:
        case ["固定",r]:
            return mkTag(("Fix",r))
        case ["浮动",r,{"基准":idx,"利差":spd,"重置频率":p}]:
            return mkTag(("Floater",[idx,spd,r,freqMap[p],None]))

def mkAsset(x):
    _typeMapping = {"等额本息": "Level", "等额本金": "Even"}
    _statusMapping = {"正常": mkTag(("Current")), "违约": mkTag(("Defaulted",None))}
    match x:
        case ["按揭贷款"
            ,{"放款金额": originBalance, "放款利率": originRate, "初始期限": originTerm
                  ,"频率": freq, "类型": _type, "放款日": startDate}
            ,{"当前余额": currentBalance
             ,"当前利率": currentRate
             ,"剩余期限": remainTerms
             ,"状态": status}
              ]:
            return mkTag(("Mortgage",[{"originBalance": originBalance,
                     "originRate": mkAssetRate(originRate),
                     "originTerm": originTerm,
                     "period": freqMap[freq],
                     "startDate": startDate,
                     "prinType": _typeMapping[_type]},
                    currentBalance,
                    currentRate,
                    remainTerms,
                    _statusMapping[status]
                    ]))

def mkCf(x):
    if len(x)==0:
        return None
    else:
        return [ mkTag(("MortgageFlow",_x+([0.0]*5))) for _x in x]

def readIssuance(pool):
    if '发行' not in pool.keys():
        return None
    issuanceField = {
        "资产池规模":"IssuanceBalance"
    }
    r = {} 
    for k,v in pool['发行'].items():
        r[issuanceField[k]] = v

    return r

def mkCollection(xs):
    sourceMapping = {"利息回款": "CollectedInterest"
                    , "本金回款": "CollectedPrincipal"
                    , "早偿回款": "CollectedPrepayment"
                    , "回收回款": "CollectedRecoveries"}
    return [[sourceMapping[x], acc] for (x, acc) in xs]


#"{\"tag\":\"PatternInterval\",
#  \"contents\":
#    {\"ClosingDate\": [\"2022-01-01\",{\"tag\":\"MonthFirst\"},\"2030-01-01\"]
#    ,\"CutoffDate\":[\"2022-01-01\",{\"tag\":\"MonthFirst\"},\"2030-01-01\"]
#    ,\"FirstPayDate\":[\"2022-02-25\",{\"tag\":\"DayOfMonth\",\"contents\":25},\"2030-01-01\"]}}"



def mkLiqProviderType(x):
    match x:
        case {"总额度": amt}:
            return mkTag(("FixSupport"))
        case {"日期":dp, "限额":amt}:
            return mkTag(("ReplenishSupport", [mkDatePattern(dp),amt]))
        case {}:
            return mkTag(("UnLimit"))

def mkLiqProvider(n, x):
    match x:
        case {"类型":"无限制","起始日":_sd, **p}: 
            return {"liqName": n, "liqType": mkLiqProviderType({})
                   , "liqBalance": None
                   , "liqCredit": p.get("已提供", 0)
                   , "liqStart": _sd}
        case {"类型": _sp, "额度": _ab, "起始日":_sd, **p}: 
            return {"liqName": n, "liqType": mkLiqProviderType(_sp)
                   , "liqBalance": _ab
                   , "liqCredit": p.get("已提供", 0)
                   , "liqStart": _sd}
        case _:
            raise RuntimeError(f"无法匹配流动性支持类型：{n,x}")


def mkComponent(x):
    match x:
        case {"贴现日": pricingDay, "贴现曲线": xs}:
            return [pricingDay, {"tag": "PricingCurve", "contents": xs}]
        case _:
            None


def mkLiq(x):
    match x:
        case {"正常余额折价": cf, "违约余额折价": df}:
            return mkTag(("BalanceFactor", [cf, df]))
        case {"贴现计价": df, "违约余额回收率": r}:
            return mkTag(("PV", [df, r]))


def mkCallOptions(x):
    match x:
        case {"资产池余额": bal}:
            return mkTag(("PoolBalance", bal))
        case {"债券余额": bal}:
            return mkTag(("PoolBalance", bal))
        case {"资产池余额剩余比率": factor}:
            return mkTag(("PoolFactor", factor))
        case {"债券余额剩余比率": factor}:
            return mkTag(("PoolFactor", factor))
        case {"指定日之后": d}:
            return mkTag(("AfterDate", d))
        case {"任意满足": xs}:
            return mkTag(("Or", xs))
        case {"全部满足": xs}:
            return mkTag(("And", xs))

#"{\"tag\":\"PrepaymentFactors\",\"contents\":{\"tag\":\"FactorCurveClosed\",\"contents\":[[\"2022-01-01\",{\"numerator\":33,\"denominator\":25}]]}}"

def mkAssumption(x):
    match x:
        case {"CPR": cpr} if isinstance(cpr,list):
            return mkTag(("PrepaymentCPRCurve", cpr))
        case {"CPR": cpr} :
            return mkTag(("PrepaymentCPR", cpr))
        case {"CPR调整": [*cprAdj,eDate]} :
            return mkTag(("PrepaymentFactors" , mkTs("FactorCurveClosed",[cprAdj,eDate])))
        case {"CDR": cdr}:
            return mkTag(("DefaultCDR", cdr))
        case {"CDR调整": [*cdrAdj,eDate]} :
            return mkTag(("DefaultFactors" , mkTs("FactorCurveClosed",[cdrAdj,eDate])))
        case {"回收": (rr, rlag)}:
            return mkTag(("Recovery", (rr, rlag)))
        case {"利率": [idx, rate]} if isinstance(rate, float):
            return mkTag(("InterestRateConstant", [idx, rate]))
        case {"利率": [idx, *rateCurve]}:
            return mkTag(("InterestRateCurve", [idx, *rateCurve]))
        case {"清仓": opts}:
            return mkTag(("CallWhen",[mkCallOptions(co) for co in opts]))
        case {"停止": d}:
            return mkTag(("StopRunBy",d))

def mkAccTxn(xs):
    "AccTxn T.Day Balance Amount Comment"
    if xs is None:
        return None
    else:
        return [ mkTag(("AccTxn",x)) for x in xs]

# \"overrides\":[[{\"tag\":\"RunWaterfall\",\"contents\":[\"2022-01-01\",\"base\"]},{\"tag\":\"PoolCollection\",\"contents\":[\"0202-11-01\",\"collection\"]}]]}


def mkComponent(x):
    match x:
        case {"贴现日": pricingDay, "贴现曲线": xs}:
            return [pricingDay, {"tag": "PricingCurve", "contents": xs}]
        case _:
            None


def mkLiq(x):
    match x:
        case {"正常余额折价": cf, "违约余额折价": df}:
            return mkTag(("BalanceFactor", [cf, df]))
        case {"贴现计价": df, "违约余额回收率": r}:
            return mkTag(("PV", [df, r]))


def mkCallOptions(x):
    match x:
        case {"资产池余额": bal}:
            return mkTag(("PoolBalance", bal))
        case {"债券余额": bal}:
            return mkTag(("PoolBalance", bal))
        case {"资产池余额剩余比率": factor}:
            return mkTag(("PoolFactor", factor))
        case {"债券余额剩余比率": factor}:
            return mkTag(("PoolFactor", factor))
        case {"指定日之后": d}:
            return mkTag(("AfterDate", d))
        case {"任意满足": xs}:
            return mkTag(("Or", xs))
        case {"全部满足": xs}:
            return mkTag(("And", xs))

#"{\"tag\":\"PrepaymentFactors\",\"contents\":{\"tag\":\"FactorCurveClosed\",\"contents\":[[\"2022-01-01\",{\"numerator\":33,\"denominator\":25}]]}}"

def mkAssumption(x):
    match x:
        case {"CPR": cpr} if isinstance(cpr,list):
            return mkTag(("PrepaymentCPRCurve", cpr))
        case {"CPR": cpr} :
            return mkTag(("PrepaymentCPR", cpr))
        case {"CPR调整": [*cprAdj,eDate]} :
            return mkTag(("PrepaymentFactors" , mkTs("FactorCurveClosed",[cprAdj,eDate])))
        case {"CDR": cdr}:
            return mkTag(("DefaultCDR", cdr))
        case {"CDR调整": [*cdrAdj,eDate]} :
            return mkTag(("DefaultFactors" , mkTs("FactorCurveClosed",[cdrAdj,eDate])))
        case {"回收": (rr, rlag)}:
            return mkTag(("Recovery", (rr, rlag)))
        case {"利率": [idx, rate]} if isinstance(rate, float):
            return mkTag(("InterestRateConstant", [idx, rate]))
        case {"利率": [idx, *rateCurve]}:
            return mkTag(("InterestRateCurve", [idx, *rateCurve]))
        case {"清仓": opts}:
            return mkTag(("CallWhen",[mkCallOptions(co) for co in opts]))
        case {"停止": d}:
            return mkTag(("StopRunBy",d))

def mkAccTxn(xs):
    "AccTxn T.Day Balance Amount Comment"
    if xs is None:
        return None
    else:
        return [ mkTag(("AccTxn",x)) for x in xs]

# \"overrides\":[[{\"tag\":\"RunWaterfall\",\"contents\":[\"2022-01-01\",\"base\"]},{\"tag\":\"PoolCollection\",\"contents\":[\"0202-11-01\",\"collection\"]}]]}
def mkOverrides(m):
    return None
#data WhenTrigger = EndCollection
#                 | EndCollectionWF
#                 | BeginDistributionWF
#                 | EndDistributionWF
class 时间点(Enum):
    回收后 = "BeginDistributionWF"
    回收动作完成后 = "EndCollectionWF"
    分配前 = "BeginDistributionWF"
    分配后 = "EndDistributionWF"

# [
#  [[\"BeginDistributionWF\",{\"tag\":\"AfterDate\",\"contents\":\"2022-03-01\"}]
#    ,{\"tag\":\"DealStatusTo\",\"contents\":{\"tag\":\"Revolving\"}}]
#   ]"
def mkTrigger(m):
    match m :
        case _:
            return None

def mkCustom(x):
    match x:
        case {"常量":n}:
            return mkTag(("CustomConstant",n))
        case {"余额曲线":ts}:
            return mkTag(("CustomCurve",mkTs("BalanceCurve",ts)))
        case {"公式":ds}:
            return mkTag(("CustomDS",mkDs(ds)))

def mk(x):
    match x:
        case ["资产", assets]:
            return {"assets": [mkAsset(a) for a in assets]}
        case ["账户", accName, attrs]:
            match attrs:
                case {"余额": bal, "类型": accType}:
                    return {accName: {"accBalance": bal, "accName": accName
                                      , "accType": mkAccType(accType)
                                      , "accInterest": mkAccInt(attrs.get("计息",None))
                                      , "accStmt": mkAccTxn(attrs.get("记录",None))}}
                case {"余额": bal}:
                    return { accName: {"accBalance": bal, "accName": accName
                                      , "accType": None
                                      , "accInterest": mkAccInt(attrs.get("计息",None))
                                      , "accStmt": mkAccTxn(attrs.get("记录",None))}}
        case ["费用", feeName, {"类型": feeType ,**fi}]:
            return {feeName: {"feeName": feeName, "feeType": mkFeeType(feeType), "feeStart":fi.get("起算日",None)
                             ,"feeDueDate":fi.get("计算日",None) , "feeDue": 0,
                              "feeArrears": 0, "feeLastPaidDay": None}}
        case ["债券", bndName, {"当前余额": bndBalance
            , "当前利率": bndRate
            , "初始余额": originBalance
            , "初始利率": originRate
            , "起息日": originDate
            , "利率": bndInterestInfo
            , "债券类型": bndType
                              }]:
            return {bndName:
                        {"bndName": bndName
                            , "bndBalance": bndBalance
                            , "bndRate": bndRate
                            , "bndOriginInfo":
                             {"originBalance": originBalance
                                 , "originDate": originDate
                                 , "originRate": originRate}
                            , "bndInterestInfo": mkBondRate(bndInterestInfo)
                            , "bndType": mkBondType(bndType)
                            , "bndDuePrin": 0
                            , "bndDueInt": 0
                            , "bndDueIntDate": None
                         }}
        case ["分配规则", instruction]:
            return mkWaterfall(instruction)
        case ["归集规则", collection]:
            return mkCollection(collection)
        case ["清仓回购", calls]:
            return mkCall(calls)

def readStatus(s):
    if "," in s:
        return s.split(",")[1]
    else:
        return mkTag("Amortizing")


@dataclass
class 信贷ABS:
    名称: str
    日期: dict
    资产池: dict
    账户: tuple
    债券: tuple
    费用: tuple
    分配规则: dict
    归集规则: tuple
    清仓回购: tuple 
    流动性支持:dict
    自定义: dict 
    触发事件: dict = field(default_factory=dict)


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
        #cutoff, closing, first_pay = mkDate(self.日期)
        stated = False # self.日期.get("法定到期日",None) if len(self.日期)==4  # if isinstance(self.日期,dict) else self.日期[3]
        dists,collects,cleans = [ self.分配规则.get(wn,[]) for wn in ['未违约','回款后','清仓回购'] ]
        distsAs,collectsAs,cleansAs = [ [ mkWaterfall2(_action) for _action in _actions] for _actions in [dists,collects,cleans] ]
        distsflt,collectsflt,cleanflt = [ itertools.chain.from_iterable(x) for x in [distsAs,collectsAs,cleansAs] ]
        status = readStatus(self.名称)
        parsedDates = mkDate(self.日期)
        """
        get the json formatted string
        """
        _r = {
            "dates": parsedDates,
            "name": self.名称,
            "status":status,
            "pool":{"assets": [mkAsset(x) for x in self.资产池.get('清单',[])]
                , "asOfDate": self.日期['封包日']
                , "issuanceStat": readIssuance(self.资产池)
                , "futureCf":mkCf(self.资产池.get('归集表', []))
                },
            "bonds": functools.reduce(lambda result, current: result | current
                                      , [mk(['债券', bn, bo]) for (bn, bo) in self.债券]),
            "waterfall": {f"DistributionDay {status['tag']}":list(distsflt)
                        , "EndOfPoolCollection": list(collectsflt)
                        , "CleanUp": list(cleanflt)},
            "fees": functools.reduce(lambda result, current: result | current
                                     , [mk(["费用", feeName, feeO]) for (feeName, feeO) in self.费用]) if self.费用 else {},
            "accounts": functools.reduce(lambda result, current: result | current
                                         , [mk(["账户", accName, accO]) for (accName, accO) in self.账户]),
            "collects": mkCollection(self.归集规则)
        }
        
        for fn, fo in _r['fees'].items():
            if fo['feeStart'] is None :
                fo['feeStart'] = self.日期["起息日"]

        if hasattr(self, "自定义") and self.自定义 is not None:
            _r["custom"] = {}
            for n,ci in self.自定义.items():
                _r["custom"][n] = mkCustom(ci)
        
        if hasattr(self, "触发事件"):
            _r["triggers"] = mkTrigger(self.触发事件)
        
        if hasattr(self, "流动性支持") and self.流动性支持 is not None:
            _providers = {}
            for (_k, _p) in self.流动性支持.items():
                _providers[_k] = mkLiqProvider(_k, ( _p | {"起始日": self.日期["起息日"]}))
            _r["liqProvider"] = _providers
        return _r  # ,ensure_ascii=False)

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
            return mkComponent(pricing)
        return None

    def read(self, resp, position=None):
        read_paths = {'bonds': ('bndStmt', ["日期", "余额", "利息", "本金", "执行利率", "本息合计", "备注"], "债券")
                    , 'fees': ('feeStmt', ["日期", "余额", "支付", "剩余支付", "备注"], "费用")
                    , 'accounts': ('accStmt', ["日期", "余额", "变动额", "备注"], "账户")
                    , 'liqProvider': ('liqStmt', ["日期", "限额", "变动额", "已提供","备注"], "流动性支持")
                    }
        output = {}
        for comp_name, comp_v in read_paths.items():
            if (not comp_name in resp[0]) or (resp[0][comp_name] is None):
                continue
            output[comp_name] = {}
            for k, x in resp[0][comp_name].items():
                ir = None
                if x[comp_v[0]]:
                    ir = [_['contents'] for _ in x[comp_v[0]]]
                output[comp_name][k] = pd.DataFrame(ir, columns=comp_v[1]).set_index("日期")
            output[comp_name] = collections.OrderedDict(sorted(output[comp_name].items()))
        # aggregate fees
        output['fees'] = {f: v.groupby('日期').agg({"余额": "min", "支付": "sum", "剩余支付": "min"})
                          for f, v in output['fees'].items()}

        # aggregate liquidation provider 
        #if 'liqProvider' in output:
        #    output['liqProvider'] = {l:v.   
        #                             for l,v in output['liqProvider'].items() }

        # aggregate accounts
        agg_acc = {}
        for k,v in output['accounts'].items():
            acc_by_date = v.groupby("日期")
            acc_txn_amt = acc_by_date.agg(变动额=("变动额", sum))
            ending_bal_column = acc_by_date.last()['余额'].rename("期末余额")
            begin_bal_column = ending_bal_column.shift(1).rename("期初余额")
            agg_acc[k] = acc_txn_amt.join([begin_bal_column,ending_bal_column])
            if agg_acc[k].empty:
                agg_acc[k].columns = ['期初余额', "变动额", '期末余额']
                continue
            fst_idx = agg_acc[k].index[0]
            agg_acc[k].at[fst_idx, '期初余额'] = round(agg_acc[k].at[fst_idx, '期末余额'] - agg_acc[k].at[fst_idx, '变动额'], 2)
            agg_acc[k] = agg_acc[k][['期初余额', "变动额", '期末余额']]

        output['agg_accounts'] = agg_acc

        output['pool'] = {}
        output['pool']['flow'] = pd.DataFrame([_['contents'] for _ in resp[0]['pool']['futureCf']]
                                              , columns=["日期", "未偿余额", "本金", "利息", "早偿金额", "违约金额", "回收金额", "损失", "利率"])
        output['pool']['flow'] = output['pool']['flow'].set_index("日期")
        output['pool']['flow'].index.rename("日期", inplace=True)

        output['pricing'] = pd.DataFrame.from_dict(resp[3]
                                                   , orient='index'
                                                   , columns=["估值", "票面估值", "WAL", "久期", "应计利息"]).sort_index() if resp[3] else None
        if position:
            output['position'] = {}
            for k,v in position.items():
                if k in output['bonds']:
                    b = self._get_bond(k)
                    factor = v / b["初始余额"] / 100
                    if factor > 1.0:
                        raise  RuntimeError("持仓系数大于1.0")
                    output['position'][k] = output['bonds'][k][['本金','利息','本息合计']].apply(lambda x:x*factor).round(4)

        return output

def loadAsset(fp, reader, astType):
    ''' load assets '''
    with open(fp, 'r') as f:
        reader = csv.DictReader(f)
        return [ r for  r in reader ]


def show(r, x="full"):
    _comps = ['agg_accounts', 'fees', 'bonds']

    dfs = { c:pd.concat(r[c].values(), axis=1, keys=r[c].keys())
                             for c in _comps if r[c] }

    dfs2 = {}
    _m = {"agg_accounts":"账户","fees":"费用","bonds":"债券"}
    for k,v in dfs.items():
        dfs2[_m[k]] = pd.concat([v],keys=[_m[k]],axis=1)

    agg_pool = pd.concat([r['pool']['flow']], axis=1, keys=["资产池"])
    agg_pool = pd.concat([agg_pool], axis=1, keys=["资产池"])

    _full = functools.reduce(lambda acc,x: acc.merge(x,how='outer',on=["日期"]),[agg_pool]+list(dfs2.values()))

    match x:
        case "full":
            return _full.loc[:, ["资产池"]+list(dfs2.keys())].sort_index()
        case "cash":
            return None # ""

def flow_by_scenario(rs, flowpath,annotation=True,aggFunc=None,rnd=2):
    "pull flows from multiple scenario"
    scenario_names = rs.keys()
    dflow = None
    aggFM = {"max":pd.Series.max,"sum":pd.Series.sum,"min":pd.Series.min}
    
    if aggFunc is None:
        dflows = [query(rs,[s]+flowpath) for s in scenario_names]
    else:
        dflows = [query(rs,[s]+flowpath).groupby("日期").aggregate(aggFM.get(aggFunc,aggFunc)) for s in scenario_names]
        
    if annotation:
        dflows = [f.rename(f"{s}({flowpath[-1]})") for (s,f) in zip(scenario_names,dflows)]
    try: 
        return pd.concat(dflows,axis=1).round(rnd)
    except ValueError as e:
        return f"需要传入 aggFunc 函数对重复数据进行 Min/Max/Sum 处理"


import matplotlib.pyplot as plt
from matplotlib import font_manager

def init_plot_fonts():
    define_list = ['Source Han Serif CN','Microsoft Yahei','STXihei']
    support_list = font_manager.findSystemFonts(fontpaths=None, fontext='ttf')
    font_p = font_manager.FontProperties()
    try:
        for sl in support_list:
            f = font_manager.get_font(sl)
            if f.family_name in set(define_list):
                font_p.set_family(f.family_name)
                font_p.set_size(14)
                return font_p
    except RuntimeError as e:
        logging.error("中文字体载入失败")
        return None

font_p = init_plot_fonts()

def plot_bond(rs, bnd, flow='本息合计'):
    """Plot bonds across scenarios"""
    plt.figure(figsize=(12,8))
    _alpha =  0.8
    for idx,s in enumerate(rs):
        plt.step(s['bonds'][bnd].index,s['bonds'][bnd][[flow]], alpha=_alpha, linewidth=5, label=f"场景-{idx}")

    plt.legend(loc='upper left', prop=font_p)
    plt.title(f'{len(rs)} 种场景下 债券:{bnd} - {flow}', fontproperties=font_p)

    plt.grid(True)
    plt.axis('tight')
    plt.xticks(rotation=30)

    current_values = plt.gca().get_yticks()
    plt.gca().set_yticklabels(['{:.0f}(w)'.format(x/10000) for x in current_values])
    return plt

def plot_bonds(r, bnds:list, flow='本息合计'):
    "Plot bond flows with in a single run"
    plt.figure(figsize=(12,8))
    _alpha =  0.8
    for b in bnds:
        b_flow = r['bonds'][b]
        plt.step(b_flow.index,b_flow[[flow]], alpha=_alpha, linewidth=5, label=f"债券-{b}")

    plt.legend(loc='upper left', prop=font_p)
    bnd_title = ','.join(bnds)
    plt.title(f'债券:{bnd_title} - {flow}', fontproperties=font_p)

    plt.grid(True)
    plt.axis('tight')
    plt.xticks(rotation=30)

    current_values = plt.gca().get_yticks()
    plt.gca().set_yticklabels(['{:.0f}(w)'.format(x/10000) for x in current_values])
    return plt

def plot_by_scenario(rs, flowtype, flowpath):
    "Plot with multiple scenario"
    plt.figure(figsize=(12,8))
    scenario_names = rs.keys()
    dflows = [query(rs,[s]+flowpath) for s in scenario_names]
    _alpha =  0.8

    x_labels = reduce(lambda acc,x:acc.union(x) ,[ _.index for _ in dflows ]).unique()
    x = np.arange(len(x_labels))
    width = 1 
    step_length = width / (len(scenario_names)+1)

    for (idx,(scen,dflow)) in enumerate(zip(scenario_names,dflows)):
        if flowtype=="balance":
            cb = consolStmtByDate(dflow)
            plt.step(cb.index, cb, alpha=_alpha, linewidth=5, label=f"{scen}")
        elif flowtype=="amount":
            cb = aggStmtByDate(dflow)
            _bar = plt.bar(x+idx*step_length,cb,width=step_length,label=scen)
        else:
            plt.plot(dflow.index,dflow, alpha=_alpha, linewidth=5, label=f"{scen}")

    plt.legend(scenario_names,loc='upper right', prop=font_p)
    plt.grid(True)
    plt.axis('tight')
    plt.xticks(ticks=x,labels=x_labels,rotation=30)

