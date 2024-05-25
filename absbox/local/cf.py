import pandas as pd
import toolz as tz
from lenses import lens
from functools import reduce
#from itertools import reduce


def readToCf(xs, header=None, idx=None, sort_index=False) -> pd.DataFrame:
    ''' input with flow type json, return a dataframe '''
    rows = [_['contents'] for _ in xs]

    if header:
        r = pd.DataFrame(rows, columns=header)
    else:
        r = pd.DataFrame(rows)

    if idx:
        r = r.set_index(idx)

    if sort_index:
        r = r.sort_index()

    return r


def readBondsCf(bMap, popColumns=["factor","memo","本金系数","备注","应付利息","罚息","intDue","intOverInt"]) -> pd.DataFrame:
    def isBondGroup(k,v) -> bool:
        if isinstance(k,str) and isinstance(v,dict):
            return True
        else:
            return False
    
    def hasBondGroup():
        _r = [ isBondGroup(k,v) for k,v in bMap.items()]
        return any(_r)
        
    def filterCols(x:dict, columnsToKeep) -> pd.DataFrame:
        return lens.Recur(pd.DataFrame).modify(lambda z: z[columnsToKeep])(x)
   
    bondNames = list(bMap.keys())
    if not bondNames:
        return pd.DataFrame()

    # colums of bond from resp
    bondColumns = (bMap & lens.Recur(pd.DataFrame).get()).columns.to_list()

    # columns to show for each bond
    columns = list(filter(lambda x: x not in set(popColumns), bondColumns))
    
    if not hasBondGroup():
        header = pd.MultiIndex.from_product([bondNames,columns]
                                            , names=['Bond',"Field"])
        yyz = list(filterCols(bMap, columns).values())
        df = pd.concat(yyz,axis=1)
        
    else:
        bMap = filterCols(bMap, columns)
        bondDf = bMap & lens.Recur(pd.DataFrame).collect()
        indexes = []
        cfFrame = []
        for k,v in bMap.items():
            if isBondGroup(k,v):
                for _k,_v in v.items(): # sub bonds
                    indexes.extend([(k,_k,_) for _ in columns])
                    cfFrame.append(_v)
            else:
                if bMap[k] is not None:
                    indexes.extend([(k,"-",_) for _ in columns])
                    cfFrame.append(v)
                    
        header = pd.MultiIndex.from_tuples(indexes
                                          , names=["BondGroup",'Bond',"Field"]
                                          )       
        
        df = pd.concat(cfFrame,axis=1)
    df.columns = header
    return df.sort_index()


def readFeesCf(fMap, popColumns=["due","剩余支付"]) -> pd.DataFrame:
    def filterCols(xs, columnsToKeep):
        return [ _[columnsToKeep]  for _ in xs ]
    
    feeNames = list(fMap.keys())
    feeColumns = list(fMap.values())[0].columns.to_list()
    columns = list(filter(lambda x: x not in set(popColumns), feeColumns))
    header = pd.MultiIndex.from_product([feeNames, columns]
                                        , names=['Fee',"Field"])
    
    df = pd.concat(filterCols(list(fMap.values()),columns),axis=1)
    df.columns = header
    return df

def readAccsCf(aMap, popColumns=["memo"]) -> pd.DataFrame:
    def filterCols(xs, columnsToKeep):
        return [ _[columnsToKeep]  for _ in xs ]
    
    accNames = list(aMap.keys())
    accColumns = list(aMap.values())[0].columns.to_list()
    columns = list(filter(lambda x: x not in set(popColumns) , accColumns))
    header = pd.MultiIndex.from_product([accNames, columns]
                                        , names=['Account',"Field"])

    df = pd.concat(filterCols(list(aMap.values()),columns),axis=1)
    df.columns = header
    return df

def readFlowsByScenarios(rs:dict, path, fullName=True) -> pd.DataFrame:
    "read time-series balance from multi scenario or mult-structs"
    
    flows = tz.valmap(lambda x: x & path.get(), rs)
    
    if fullName:
        flows = tz.itemmap(lambda kv: (kv[0],kv[1].rename(f"{kv[0]}:{kv[1].name}"))   ,flows)
    
    return pd.concat(flows.values(),axis=1)

def readMultiFlowsByScenarios(rs:dict, _path, fullName=True) -> pd.DataFrame:
    "read multi time-series from multi scenario or mult-structs"
    
    (path,cols) = _path
    _flows = tz.valmap(lambda x: x & path.get(), rs)
    flows = tz.valmap(lambda df: df[cols],_flows)
    scenarioNames = list(flows.keys())
    header = pd.MultiIndex.from_product([ scenarioNames ,cols], names =['Scenario',"Field"])

    df = pd.concat( list(flows.values()) ,axis=1)
    df.columns = header
    return df
