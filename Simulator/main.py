#!/usr/bin/pypy3

import argparse
from TraceReader import TraceReader
from FlashTranslationLayer import FlashTranslationLayer
from SSDConfig import SSDConfig
from Utilities import Utilities
import time, sys

def main():
    parser = argparse.ArgumentParser(description="SSD FTL Simulator")
    
    # Positional arguments: ftlScheme, trace_file, ssd_config
    parser.add_argument("ftlScheme", help="FTL Scheme (e.g., DFTL, CDFTL, CCFTL, rFTL)")
    parser.add_argument("trace_file", help="Path to the trace file")
    parser.add_argument("ssd_config", help="Path to the base SSD configuration file")
    
    # Optional override arguments
    parser.add_argument("--struct", help="Override Path to SSD Structure configuration")
    parser.add_argument("--gc_wl_tran", help="Override Path to GC/WL/Translation configuration")
    parser.add_argument("--cache", help="Override Path to Cache/Mapping configuration")
    parser.add_argument("--timing", help="Override Path to Timing configuration")
    parser.add_argument("--meta", help="Override Path to MetaData configuration")

    args = parser.parse_args()

    # Load base configuration
    config_dict = Utilities.load_config_from_file(args.ssd_config)
    
    # Apply overrides if provided
    overrides = {
        "struct": args.struct,
        "gc_wl_tran": args.gc_wl_tran,
        "cache": args.cache,
        "timing": args.timing,
        "meta": args.meta
    }
    
    for key, path in overrides.items():
        if path:
            override_data = Utilities.load_config_from_file(path)
            config_dict.update(override_data)
            
    cfg = SSDConfig.get_SSDConfig_from_dict(config_dict)
    
    ftlScheme  = args.ftlScheme
    trace_file = args.trace_file

    if ftlScheme == "DFTL":
        cfg.cmt_ctp_entry_size = 0

    if ftlScheme == "rFTL":
        cfg.cmt_ctp_entry_size += 4
        cfg.rGTD_GROUP = 8

    tracer = TraceReader(trace_file, cfg.page_size)
    ftl    = FlashTranslationLayer(ftlScheme, cfg)
    
    loop_count = 0
    isOK = True
    while True:
        loop_count += 1
        req = tracer.read_request()
        if req == None:
            break
        # print(f"[{loop_count:>6}]: Operation: {req.req_type},  LPA: {req.lpa}, size: {req.size_pages}")
        # TPIndex = req.lpa // 1024
        # Group, Offset = divmod(TPIndex, cfg.rGTD_GROUP)
        # print(f"TPIndex: {TPIndex}, Group: {Group}, Offset: {Offset}")

        lpa        = req.lpa
        size_pages = req.size_pages

        if req.isReadReq():
            ftl.reads(lpa, size_pages)
        elif req.isWriteReq():
            remaining_pages = req.size_pages

            while remaining_pages > 0:
                size_pages = min(remaining_pages, 16)

                isOK = ftl.writes(lpa, size_pages, [req.size_pages for _ in range(size_pages)], time.time_ns())

                lpa += size_pages
                remaining_pages -= size_pages
        else:
            raise Exception("ERROR!!! Can't determine I/O type!")

        if isOK == False:
            break

    tracer.close()

    ftl.flush_cmt()

    
    # print final status
    ftl.print_status()
    #ftl.dump_gtd()
    #ftl.dump_block_manager()
    #ftl.dump_flash_array()

if __name__ == "__main__":
    main()    
