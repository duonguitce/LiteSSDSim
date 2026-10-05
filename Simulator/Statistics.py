from collections import defaultdict

class Statistics:  
    def __init__(self):
        # Host-side statistics (user requests)
        self.host_read_pages = defaultdict(int)      # the number of times each LPA page is read by host requests
        self.host_write_pages = defaultdict(int)     # the number of times each LPA page is written by host requests
        
        # Flash-side statistics (tới flash hardware)
        self.flash_read_pages = defaultdict(int)     # the number of times each PPA page is read from flash
        self.flash_write_pages = defaultdict(int)    # the number of times each PPA page is written to flash
        self.flash_erase_blocks = defaultdict(int)   # the number of times each block is erased
        
        # Cache statistics
        self.cmt_hits = 0           # CMT hit count (mapping cache hit)
        self.cmt_misses = 0         # CMT miss count
        self.cmt_hits_by_page = 0   # the number of page hits in CMT (total number of page accesses that hit in CMT)
        self.cmt_misses_by_page = 0 # the number of page misses in CMT (total number of page accesses that miss in CMT)
        
        # Counters for WAF calculation
        self.total_host_writes = 0     # total number of pages written by host requests
        self.total_flash_writes = 0    # total number of pages written to flash (including GC/WL/translation writes)
        self.total_host_reads = 0      # total number of pages read from host
        self.total_flash_reads = 0     # total number of pages read from flash
        
        # Page-level request counters
        self.total_host_read_requests = 0   # total request from host (don't page count)
        self.total_host_write_requests = 0  # total request from host

        # OperationEngine-level statistics
        self.operation_data_read_pages = 0
        self.operation_host_data_write_pages = 0
        self.operation_gc_data_write_pages = 0
        self.operation_wl_data_write_pages = 0
        self.operation_translation_write_pages = 0
        self.operation_translation_relocation_pages = 0

        # GC/WL statistics
        self.gc_runs = 0
        self.gc_victim_blocks = 0
        self.gc_migrated_data_pages = 0
        self.gc_migrated_translation_pages = 0
        self.wl_runs = 0
        self.wl_migrated_pages = 0

        # Translation page usage statistics
        self.translation_pages_used = 0
        self.valid_flash_mapping_entries_per_translation_page = {}
        self.avg_valid_flash_mapping_entries_per_translation_page = 0.0
        self.avg_valid_flash_mappings_per_translation_page = 0.0
        self.total_mappings_used = 0
        
    # ====== Host-side tracking ======
    def track_host_read(self, lpa: int, size: int):
        for page in range(lpa, lpa + size):
            self.host_read_pages[page] += 1
        self.total_host_reads += size
        self.total_host_read_requests += 1
    
    def track_host_write(self, lpa: int, size: int):
        for page in range(lpa, lpa + size):
            self.host_write_pages[page] += 1
        self.total_host_writes += size
        self.total_host_write_requests += 1
    
    # ====== Flash-side tracking ======
    def track_flash_read(self, ppa: int):
        self.flash_read_pages[ppa] += 1
        self.total_flash_reads     += 1
    
    def track_flash_write(self, ppa: int):
        self.flash_write_pages[ppa] += 1
        self.total_flash_writes     += 1
    
    def track_flash_erase(self, block_id: int):
        self.flash_erase_blocks[block_id] += 1
    
    # ====== Cache tracking ======
    def track_cmt_hit(self):
        self.cmt_hits += 1
    
    def track_cmt_miss(self):
        self.cmt_misses += 1

    def track_cmt_hit_by_page(self, num: int):
        self.cmt_hits_by_page += num

    def track_cmt_miss_by_page(self, num: int):
        self.cmt_misses_by_page += num


    # ====== OperationEngine tracking ======
    def track_operation_data_read(self, size: int):
        self.operation_data_read_pages += size

    def track_operation_host_data_write(self, size: int):
        self.operation_host_data_write_pages += size

    def track_operation_gc_data_write(self, size: int):
        self.operation_gc_data_write_pages += size

    def track_operation_wl_data_write(self, size: int):
        self.operation_wl_data_write_pages += size

    def track_operation_translation_write(self, size: int = 1):
        self.operation_translation_write_pages += size

    def track_operation_translation_relocation(self, size: int = 1):
        self.operation_translation_relocation_pages += size

    # ====== GC/WL tracking ======
    def track_gc_run(self, victim_blocks: int, migrated_data_pages: int, migrated_translation_pages: int):
        self.gc_runs += 1
        self.gc_victim_blocks += victim_blocks
        self.gc_migrated_data_pages += migrated_data_pages
        self.gc_migrated_translation_pages += migrated_translation_pages

    def track_wl_run(self, migrated_pages: int):
        self.wl_runs += 1
        self.wl_migrated_pages += migrated_pages

    # ====== Translation page usage tracking ======
    def track_translation_page_usage(self, valid_entries_per_tp: dict[int, tuple[int, int]]):
        self.valid_flash_mapping_entries_per_translation_page = dict(valid_entries_per_tp)
        self.translation_pages_used = len(valid_entries_per_tp)
        if self.translation_pages_used == 0:
            self.avg_valid_flash_mapping_entries_per_translation_page = 0.0
            self.avg_valid_flash_mappings_per_translation_page = 0.0
            self.total_mappings_used = 0
        else:
            total_valid_entries = 0
            total_valid_mappings = 0
            for a, b in valid_entries_per_tp.values():
                total_valid_entries += a
                total_valid_mappings += b
            #total_valid_entries = sum(valid_entries_per_tp.values())
            self.avg_valid_flash_mapping_entries_per_translation_page = total_valid_entries / self.translation_pages_used
            self.avg_valid_flash_mappings_per_translation_page = total_valid_mappings / self.translation_pages_used
            if total_valid_mappings == 0:
                self.total_mappings_used = total_valid_entries
            else:
                self.total_mappings_used = total_valid_mappings
    
    # ====== Calculations ======
    # Calculate Write Amplification Factor (WAF): WAF = Total Flash Writes / Total Host Writes
    def get_waf(self) -> float:
        if self.total_host_writes == 0:
            return 0.0
        return self.total_flash_writes / self.total_host_writes
    
    # Calculate Read Amplification Factor (RAF): RAF = Total Flash Reads / Total Host Reads
    def get_raf(self) -> float:
        if self.total_host_reads == 0:
            return 0.0
        return self.total_flash_reads / self.total_host_reads

    # Calculate CMT hit rate: Hit Rate = CMT Hits / (CMT Hits + CMT Misses)
    def get_cmt_hit_rate(self) -> float:
        total_cmt_accesses = self.cmt_hits + self.cmt_misses
        if total_cmt_accesses == 0:
            return 0.0
        return self.cmt_hits / total_cmt_accesses
    
    # Calculate CMT hit rate based on page accesses: Hit Rate = CMT Hits by Page / (CMT Hits by Page + CMT Misses by Page)
    def get_cmt_hit_rate_by_page(self) -> float:
        total_cmt_page_accesses = self.cmt_hits_by_page + self.cmt_misses_by_page
        if total_cmt_page_accesses == 0:
            return 0.0
        return self.cmt_hits_by_page / total_cmt_page_accesses
    
    # ====== Reporting ======
    def print_statistics(self):
        print("\n--- HOST-SIDE STATISTICS ---")
        print(f"Total Host Read Requests : {self.total_host_read_requests}")
        print(f"Total Host Write Requests: {self.total_host_write_requests}")
        print(f"Total Host Read Pages    : {self.total_host_reads}")
        print(f"Total Host Write Pages   : {self.total_host_writes}")

        print("\n--- FLASH-SIDE STATISTICS ---")
        print(f"Total Flash Read Pages      : {self.total_flash_reads}")
        print(f"Total Flash Write Pages     : {self.total_flash_writes}")
        print(f"Total Flash Erase Operations: {sum(self.flash_erase_blocks.values())}")
        print(f"Total Unique Blocks Erased  : {len(self.flash_erase_blocks)}")
        
        # if self.flash_erase_blocks:
        #     print(f"Block Erase Count (Top 10):")
        #     sorted_erases = sorted(self.flash_erase_blocks.items(), key=lambda x: x[1], reverse=True)
        #     for i, (block, count) in enumerate(sorted_erases[:10]):
        #         print(f"  {i+1}. Block {block}: {count} erases")
        
        print("\n--- WRITE AMPLIFICATION FACTOR (WAF) ---")
        waf = self.get_waf()
        print(f"WAF = {waf:.2f} (Flash Writes: {self.total_flash_writes} / Host Writes: {self.total_host_writes})")
        
        print("\n--- READ AMPLIFICATION FACTOR (RAF) ---")
        raf = self.get_raf()
        print(f"RAF = {raf:.2f} (Flash Reads: {self.total_flash_reads} / Host Reads: {self.total_host_reads})")

        print("\n--- CACHE STATISTICS ---")
        cmt_total = self.cmt_hits + self.cmt_misses
        print(f"Cached Mapping Table Hits: {self.cmt_hits}")
        print(f"CMT Misses               : {self.cmt_misses}")
        print(f"CMT Total Accesses       : {cmt_total}")
        if cmt_total > 0:
            print(f"CMT Hit Rate: {self.get_cmt_hit_rate()*100:.2f}%")

        print("-" * 40)
        cmt_total_pages = self.cmt_hits_by_page + self.cmt_misses_by_page
        print(f"CMT Hits by Page         : {self.cmt_hits_by_page}")
        print(f"CMT Misses by Page       : {self.cmt_misses_by_page}")
        print(f"CMT Total Pages Accesses : {cmt_total_pages}")
        if cmt_total_pages > 0:
            print(f"CMT Hit Rate by Page     : {self.get_cmt_hit_rate_by_page()*100:.2f}%")
        else:
            print(f"CMT Hit Rate by Page     : 0.00%")

        print("\n--- OPERATION ENGINE STATISTICS ---")
        print(f"Data Pages Read by OperationEngine        : {self.operation_data_read_pages}")
        print(f"Host Data Pages Written by OperationEngine: {self.operation_host_data_write_pages}")
        print(f"GC Data Pages Rewritten                   : {self.operation_gc_data_write_pages}")
        print(f"WL Data Pages Rewritten                   : {self.operation_wl_data_write_pages}")
        print(f"Translation Pages Written                 : {self.operation_translation_write_pages}")
        print(f"Translation Pages Relocated               : {self.operation_translation_relocation_pages}")

        print("\n--- GC/WL STATISTICS ---")
        print(f"GC Runs                      : {self.gc_runs}")
        print(f"GC Victim Blocks             : {self.gc_victim_blocks}")
        print(f"GC Migrated Data Pages       : {self.gc_migrated_data_pages}")
        print(f"GC Migrated Translation Pages: {self.gc_migrated_translation_pages}")
        print(f"WL Runs                      : {self.wl_runs}")
        print(f"WL Migrated Pages            : {self.wl_migrated_pages}")

        print("\n--- TRANSLATION PAGE USAGE ---")
        print(f"Translation Pages Used (GTD Entries): {self.translation_pages_used}")
        print(f"Average Valid entry per Translation Page: {self.avg_valid_flash_mapping_entries_per_translation_page:.2f}")
        print(f"Average Valid mapping per Translation Page: {self.avg_valid_flash_mappings_per_translation_page:.2f}")
        print(f"Total Mapings: {self.total_mappings_used}")
