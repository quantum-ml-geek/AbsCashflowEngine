import pandas as pd
import os,pickle,sys
from absbox import API
from absbox.local.china import show,信贷ABS
import requests
import json
import pprint as pp
from json.decoder import JSONDecodeError

from jsondiff import diff
from deepdiff import DeepDiff

import absbox.tests.benchmark.us as us
import absbox.tests.benchmark.china as cn

import csv,logging


china_folder = os.path.join("absbox","tests","benchmark","china")
us_folder = os.path.join("absbox","tests","benchmark","us")
test_folder = os.path.join("absbox","tests")
config_file = os.path.join(test_folder,"config.json")

with open(config_file,'r') as cfh:
    config = json.load(cfh)

def translate(input_cases,output_folder):
    case_out = output_folder
    pair = input_cases
    for d,o in pair:
        print(f"Translating>>{d}>>{o}")
        benchfile =  os.path.join(case_out,o)
        if not os.path.exists(benchfile) or os.stat(benchfile).st_size < 10 :
            print(f"Skipping:{benchfile}")
            with open(benchfile,'w',encoding='utf8') as newbench:
                try:
                    print(f"Writing new bench out case -> size {len(d.json)}")
                except Exception as e:
                    print(f"Error in build deal json:{e}")
                assert d.json is not None, f"None: Failed to generate Deal JSON file:{d.json}"
                assert d.json != "", f"Empty: Failed to generate Deal JSON file:{d.json}"
                json.dump(d.json,newbench,indent=2)
            logging.info(f"Create new case for {o}")
            continue
        with open(benchfile ,'r') as ofile:
            try:
                benchmark_out = json.load(ofile)
                if d.json != benchmark_out:
                    print(f"Failed with benchmark file:{benchfile} ")
                    diff_result = DeepDiff(d.json,benchmark_out)
                    pp.pprint(diff_result,indent=2)
                    assert d.json == benchmark_out, f"testing fail on {o}"
            except JSONDecodeError as e:
                print(f"Error parsing json format:{benchfile}")


def test_translate():
    case_out = os.path.join(china_folder, "out")
    pair = cn.translate_pair
    translate(pair, case_out)
    case_out = os.path.join(us_folder, "out")
    pair = us.translate_pair
    translate(pair, case_out)

def run_deal(input_folder, pair):
    input_req_folder = os.path.join(input_folder,"out")
    input_scen_folder = os.path.join(input_folder,"scenario")
    input_resp_folder = os.path.join(input_folder,"resp")
    test_server = config["test_server"] #https://deal-bench.xyz/api/run_deal2" 
    
    #test_server = "http://localhost:8081/run_deal2" 
    for dinput, sinput, eoutput in pair:
        print(f"Comparing:{dinput},{sinput},{eoutput}")
        with open(os.path.join(input_req_folder,dinput), 'r') as dq:  # deal request
            with open(os.path.join(input_scen_folder,sinput), 'r') as sq: # scenario request 
                print(f"With deal request=> {dinput}, scenario => {sinput}")
                req = {"deal":json.load(dq), "assump": {"tag":"Single", "contents":json.load(sq)}, "bondPricing":None}
                print("build req done")
                hdrs = {'Content-type': 'application/json', 'Accept': 'text/plain'}
                tresp = requests.post(test_server, data=json.dumps(req).encode('utf-8'), headers=hdrs, verify=False)
                if tresp.status_code != 200:
                    print(f"Failed to finish req:{req['deal']['contents']['name']}")
                    print(tresp.text)

                s_result = json.loads(tresp.text)
                local_bench_file = os.path.join(input_resp_folder,eoutput)
                if not os.path.exists(local_bench_file):
                    with open(local_bench_file,'w') as wof: # write output file
                        json.dump(s_result,wof,indent=2)
                    continue
                with open(local_bench_file,'r') as eout: # expected output 
                    local_result = json.load(eout)
                    assert local_result[1]==s_result[1],"Pool Flow Is not matching"
                    if not local_result[0]['waterfall']==s_result[0]['waterfall']:
                        assert False,f"diff in waterfall: {diff(local_result[0]['waterfall'],s_result[0]['waterfall'])}"
                    if local_result[0]['bonds']!=s_result[0]['bonds']:
                        print("Bonds are not matching")
                        for bn,bv in local_result[0]['bonds'].items():
                            if s_result[0]['bonds'][bn]!=bv:
                                print(f"Bond {bn} is not matching")
                                print(DeepDiff(s_result[0]['bonds'][bn], bv))

                    #for i in ['status','dates','pool','fees','bonds','accounts']:
                    #    assert local_result[0][i]==s_result[0][i], f"Deal {i} is not matching"
                    bench_keys = local_result[0].keys()
                    result_keys = s_result[0].keys()
                    assert set(bench_keys) == set(result_keys),f"keys are not matching: bench {bench_keys},result {result_keys}"
                    for i in ['status','dates','pool','fees','bonds','accounts']:
                        assert local_result[0][i]==s_result[0][i], f"Deal {i} is not matching"
                    assert s_result == local_result , f"Server Test Failed {dinput} {sinput} {eoutput} "


def test_resp():
    pair = [("test01.json","empty.json","test01.out.json")
            ,("test02.json","empty.json","test02.out.json")
            ,("test03.json","empty.json","test03.out.json")
            ,("test04.json","empty.json","test04.out.json")
            ,("test05.json","empty.json","test05.out.json")
            ,("test06.json","empty.json","test06.out.json")
            ,("test07.json","empty.json","test07.out.json")
            ,("test08.json","empty.json","test08.out.json")
            ,("test09.json","empty.json","test09.out.json")
            ,("test10.json","empty.json","test10.out.json")
            ,("test11.json","rates.json","test11.out.json")
            ,("test12.json","empty.json","test12.out.json")
            ,("test13.json","empty.json","test13.out.json")
            ,("test14.json","empty.json","test14.out.json")
            ,("test15.json","empty.json","test15.out.json")
            ,("test16.json","empty.json","test16.out.json")
            ,("test17.json","empty.json","test17.out.json")
            ,("test18.json","empty.json","test18.out.json")
            ,("test19.json","defaults01.json","test19.out.json")
            ,("test20.json","empty.json","test20.out.json")
            ]
    print(">>>> Runing China Bench Files")
    run_deal(china_folder, pair)
    
    print(">>>> Runing US Bench Files")
    pair = [("test01.json","empty.json","test01.out.json") ]
    run_deal(us_folder, pair)


