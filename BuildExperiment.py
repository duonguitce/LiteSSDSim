#!/usr/bin/env python3

import datetime
import os


# Get current directory
cwd = os.getcwd()

FTLScheme = [
    #"DFTL",
    #"CDFTL",
    #"CCFTL",
    "rFTL"
]
current_date = (datetime.datetime.today()).strftime("%Y_%m_%d_%H_%S")


SSDConfigDir = cwd + "/SSDConfig/Monolithic"
CacheConfigDir = cwd + "/SSDConfig/Cache_MappingCfg"
WorkloadDir  = cwd + "/Workloads"
Simulator    = cwd + "/Simulator"
OutputDir    = cwd + "/Output_" + current_date
LogDir       = OutputDir + "/Logs"
os.makedirs(LogDir)

taskFile = "Experiment_" + current_date + "_task.run"
taskHandler = open(taskFile, 'w')

for ssd_config in os.listdir(SSDConfigDir):
    if not ssd_config.endswith(".json"):
        continue

    for cache_config in os.listdir(CacheConfigDir):
        if not cache_config.endswith(".json"):
            continue

        for workload in os.listdir(WorkloadDir):
            for scheme in FTLScheme:
                workingDir = OutputDir + "/" + ssd_config.split(".")[0] + "/" + cache_config.split(".")[0] + "/" + workload.split(".csv")[0] + "/" + scheme 
                os.makedirs(workingDir)
                logFile = "log_" + ssd_config.split(".")[0] + "_" + cache_config.split(".")[0] + "_" + workload.split(".trace")[0] + "_" + scheme
                runCMD  = "cd " + workingDir + " ; " + Simulator + "/" + "main.py" + " " + scheme + " " + WorkloadDir + "/" + workload + " " + SSDConfigDir + "/" + ssd_config +  " --cache " + CacheConfigDir + "/" + cache_config + " > " + LogDir + "/" + logFile + " ; " + "cd " + cwd + " \n"
                taskHandler.write(runCMD)
    
taskHandler.close()




