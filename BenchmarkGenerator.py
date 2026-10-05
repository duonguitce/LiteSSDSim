#!/usr/bin/env python3

import random
import json
import sys
import os
import subprocess
import concurrent.futures
from dataclasses import dataclass
from pathlib import Path

# =========================================================
# SSD / FTL TRACE GENERATOR
#
# Trace format:
# <DeviceID> <LogicalPageAddress> <SizeInBytes> <Type(R/W)>
#
# Example:
# 0 1024 4096 R
# 0 2048 16384 W
#
# Features:
# - Valid mapping aware
# - Sequential / Random / Mixed workload
# - Multi-page requests
# - Boundary safe
# - Read requests guaranteed valid
#
# =========================================================


@dataclass
class BenchmarkConfig:
    prefill_percent      : int 
    benchmark_requests   : int 
    seq_percent          : int 
    read_percent         : int 
    min_pages_per_request: int 
    max_pages_per_request: int 
    device_id            : int 
    num_streams          : int = 1


class TraceGenerator:
    def __init__(
        self,
        total_pages,
        page_size,
        prefill_percent,
        benchmark_requests,
        seq_percent,
        read_percent,
        min_pages_per_request,
        max_pages_per_request,
        device_id = 0,
        seed = 2026,
        num_streams = 1
    ):

        self.random = random.Random(seed)

        # SSD config
        self.page_size      = page_size
        self.total_pages    = total_pages
        self.test_size      = total_pages * page_size

        # phase config
        self.prefill_percent    = prefill_percent
        self.benchmark_requests = benchmark_requests

        # workload config
        self.seq_percent  = seq_percent
        self.read_percent = read_percent

        # request size
        self.min_pages_per_request = min_pages_per_request
        self.max_pages_per_request = max_pages_per_request

        # device
        self.device_id = device_id

        # multi-stream sequential pointers
        self.num_streams = num_streams
        self.current_stream = 0
        self.stream_lpas = [
            (i * (self.total_pages // self.num_streams)) for i in range(self.num_streams)
        ]

        # valid mapping table
        self.valid_pages = set()

    # Generate request size
    def generate_size(self):
        pages = self.random.randint(self.min_pages_per_request, self.max_pages_per_request)
        return pages * self.page_size

    # Generate operation
    def generate_op(self):
        value = self.random.randint(1, 100)
        if value <= self.read_percent:
            return "R"
        return "W"

    # Sequential LPA with Multi-stream support
    def generate_seq_lpa(self, request_pages):
        lpa = self.stream_lpas[self.current_stream]
        
        # Advance current stream pointer
        self.stream_lpas[self.current_stream] += request_pages
        
        # Boundary check for the current stream
        max_lpa_for_stream = ((self.current_stream + 1) * (self.total_pages // self.num_streams)) - 1
        if self.stream_lpas[self.current_stream] >= max_lpa_for_stream:
             self.stream_lpas[self.current_stream] = self.current_stream * (self.total_pages // self.num_streams)
        
        # Move to next stream for the next request
        self.current_stream = (self.current_stream + 1) % self.num_streams
        return lpa

    # Random LPA
    def generate_rand_lpa(self, request_pages):
        max_start = self.total_pages - request_pages
        return self.random.randint(0, max_start)

    # Mixed workload
    def generate_write_lpa(self, request_pages):
        if self.random.randint(1, 100) <= self.seq_percent:
            return self.generate_seq_lpa(request_pages)
        return self.generate_rand_lpa(request_pages)

    # Read only valid mapped pages
    def generate_read_lpa(self, request_pages):
        if len(self.valid_pages) < request_pages:
            return None
        valid_list = list(self.valid_pages)
        for _ in range(1000):
            start_lpa = self.random.choice(valid_list)
            ok = True
            for i in range(request_pages):
                if (start_lpa + i) not in self.valid_pages:
                    ok = False
                    break
            if ok:
                return start_lpa
        return None

    # Update valid mappings after WRITE
    def update_valid_pages(self, start_lpa, request_pages):
        for i in range(request_pages):
            self.valid_pages.add(start_lpa + i)

    # Emit one request
    def emit_request(self, file, lpa, size, op):
        line = (f"{self.device_id} {lpa} {size} {op}\n")
        file.write(line)

    # Generate complete trace
    def generate(self, output_file):
        with open(output_file, "w") as file:
            target_pages = int(self.total_pages * self.prefill_percent / 100)
            written_pages = 0
            while written_pages < target_pages:
                request_pages = self.random.randint(self.min_pages_per_request, self.max_pages_per_request)
                remaining     = target_pages - written_pages
                request_pages = min(request_pages, remaining)
    
                size = request_pages * self.page_size
                lpa = self.generate_seq_lpa(request_pages)
    
                # pre-fill is always WRITE
                op = "W"
                self.emit_request(file, lpa, size, op)
                self.update_valid_pages(lpa, request_pages)
                written_pages += request_pages

            for _ in range(self.benchmark_requests):
                size = self.generate_size()
                request_pages = size // self.page_size
                op = self.generate_op()
                if op == "R": # READ
                    lpa = self.generate_read_lpa(request_pages)

                    # no valid mapping found
                    if lpa is None:
                        op = "W"
                        lpa = self.generate_write_lpa(request_pages)
                else: # WRITE
                    lpa = self.generate_write_lpa(request_pages)

                self.emit_request(file, lpa, size, op)
                # update mapping only after WRITE
                if op == "W":
                    self.update_valid_pages(lpa, request_pages)

    # Summary
    def print_summary(self, output_file):
        print("================================================")
        print("TRACE GENERATION COMPLETE")
        print("================================================")
        print(f"Output File            : {output_file}")
        print(f"Test (data) Size       : {self.test_size} bytes")
        print(f"Page Size              : {self.page_size} bytes")
        print(f"Total Pages            : {self.total_pages}")
        print(f"Pre-fill Percent       : {self.prefill_percent}%")
        print(f"Benchmark Requests     : {self.benchmark_requests}")
        print(f"Sequential Percent     : {self.seq_percent}%")
        print(f"Read Percent           : {self.read_percent}%")
        print(f"Write Percent          : {100 - self.read_percent}%")
        print(f"Request Size Range     : {self.min_pages_per_request} - {self.max_pages_per_request} pages")
        print("================================================")



def load_config_from_file(path):
    with open(path, "r") as f:
        return json.load(f)

def get_BenchmarkConfig_from_dict(d: dict[str, int]) -> BenchmarkConfig:
    return BenchmarkConfig(
        prefill_percent       = d["prefill_percent"],
        benchmark_requests    = d["benchmark_requests"],
        seq_percent           = d["seq_percent"],
        read_percent          = d["read_percent"],
        min_pages_per_request = d["min_pages_per_request"],
        max_pages_per_request = d["max_pages_per_request"],
        device_id             = d["device_id"],
        num_streams           = d.get("num_streams", 1)
    )

def build_trace_job(benchmark_config_path, total_pages: int, page_size: int, seed: int):
    benchmarkCfg = get_BenchmarkConfig_from_dict(
        load_config_from_file(benchmark_config_path)
    )

    benchmark_name = benchmark_config_path.split("/")[-1].split(".")[0]
    trace_file = "Workloads/trace_" + benchmark_name + "_seed" + str(seed) + ".trace"

    return {
        "total_pages": total_pages,
        "page_size": page_size,
        "prefill_percent": benchmarkCfg.prefill_percent,
        "benchmark_requests": benchmarkCfg.benchmark_requests,
        "seq_percent": benchmarkCfg.seq_percent,
        "read_percent": benchmarkCfg.read_percent,
        "min_pages_per_request": benchmarkCfg.min_pages_per_request,
        "max_pages_per_request": benchmarkCfg.max_pages_per_request,
        "device_id": benchmarkCfg.device_id,
        "num_streams": benchmarkCfg.num_streams,
        "seed": seed,
        "trace_file": trace_file,
    }

def generate_trace_job(job):
    traceGenerator = TraceGenerator(
        total_pages           = job["total_pages"],
        page_size             = job["page_size"],
        prefill_percent       = job["prefill_percent"],
        benchmark_requests    = job["benchmark_requests"],
        seq_percent           = job["seq_percent"],
        read_percent          = job["read_percent"],
        min_pages_per_request = job["min_pages_per_request"],
        max_pages_per_request = job["max_pages_per_request"],
        device_id             = job["device_id"],
        seed                  = job["seed"],
        num_streams           = job["num_streams"]
    )
    Path(job["trace_file"]).parent.mkdir(parents=True, exist_ok=True)
    traceGenerator.generate(job["trace_file"])
    return job["trace_file"]

def parse_size(size_str: str) -> int:
    units = {
        'k': 1024,
        'm': 1024**2,
        'g': 1024**3,
        't': 1024**4,
    }
    size_str = size_str.lower().strip()
    if size_str[-1] in units:
        return int(size_str[:-1]) * units[size_str[-1]]
    else:
        return int(size_str)

# =========================================================
# MAIN
# =========================================================
def main():
    cwd = os.getcwd()

    if len(sys.argv) < 3:
        print("Usage: python BenchmarkGenerator.py <page_size> <test_size>")
        print("Example: python BenchmarkGenerator.py 4k 1G")
        sys.exit(1)

    benchmark_config_dir = "BenchmarkConfig"

    benchmark_config_path_list = os.listdir(cwd + "/" + benchmark_config_dir)
    for i in range(len(benchmark_config_path_list)):
        benchmark_config_path_list[i] = cwd + "/" + benchmark_config_dir + "/" + benchmark_config_path_list[i]

    page_size = parse_size(sys.argv[1])
    test_size = parse_size(sys.argv[2])
    total_pages = test_size // page_size
    seed = 2026

    trace_jobs = {}
    for benchmark_config_path in benchmark_config_path_list:
        if not benchmark_config_path.endswith(".json"):
            continue
        job = build_trace_job(benchmark_config_path, total_pages, page_size, seed)
        trace_jobs[job["trace_file"]] = job

    max_workers = min(len(trace_jobs), 6)
    print(f"Generating {len(trace_jobs)} trace files with {max_workers} workers...")
    with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as executor:
        future_to_trace = {
            executor.submit(generate_trace_job, job): trace_file
            for trace_file, job in trace_jobs.items()
        }
        for future in concurrent.futures.as_completed(future_to_trace):
            trace_file = future_to_trace[future]
            try:
                future.result()
                print(f"Done: {trace_file}")
            except Exception as exc:
                print(f"FAILED: {trace_file}: {exc}")
                raise

if __name__ == "__main__":
    main()
