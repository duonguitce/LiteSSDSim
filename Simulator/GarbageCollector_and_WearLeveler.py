from BlockManager import BlockManager, BlockType, BlockState, FlashPage, PageType
from OperationEngine import OperationEngine
from SSDConfig import SSDConfig
from Statistics import Statistics
import time

class GarbageCollector_and_WearLeveler:
    def __init__(self, cfg: SSDConfig, blockManager: BlockManager, operationEngine: OperationEngine, statistics: Statistics):
        self.blockManager    = blockManager
        self.operationEngine = operationEngine
        self.statistics      = statistics

        self.gc_target       = cfg.gc_target
        self.gc_threshold    = cfg.gc_threshold
        self.wl_threshold    = cfg.wl_threshold

        self.GC_runtime = 0
        self.WL_runtime = 0
        self.GC_tracker = {}
        self.WL_highestEraseBlockID_tracker = {}
        self.WL_coldestDataBlockID_tracker  = {}

    def run(self):
        victimDataBlockIDs = self.GC_run()
        if len(victimDataBlockIDs) > 0:
            highestErasedBlockID = max(victimDataBlockIDs, key=lambda blockID: self.blockManager.blockList[blockID].nErase)
            self.WL_run(highestErasedBlockID)

    def GC_run(self) -> list[int]:
        freeDataBlockRatio  = self.blockManager.get_data_free_block_ratio()
        freeTranslationBlockRatio = self.blockManager.get_translation_free_block_ratio()
        
        if (freeDataBlockRatio > 1 - self.gc_threshold) and (freeTranslationBlockRatio > 1 - self.gc_threshold):
            return []

        victimBlockIDs = self.GC_select_victim_blockIDs()
        if len(victimBlockIDs) == 0:
            return []
        
        victimDataBlockIDs = [blockID for blockID in victimBlockIDs if self.blockManager.blockList[blockID].blockType == BlockType.DATA]
        victimTranslationBlockIDs = [blockID for blockID in victimBlockIDs if self.blockManager.blockList[blockID].blockType == BlockType.TRANSLATION]
        
        isGCDataRun = False
        isGCTranslationRun = False
        migrated_data_pages = 0
        migrated_translation_pages = 0
        
        victim_data_length = len(victimDataBlockIDs)
        if victim_data_length >= self.blockManager.total_planes:    # due to RR allocation
            isGCDataRun = True
            startIndex = 0
            while victim_data_length > 0:
                batch_size     = min(self.blockManager.get_gc_wl_free_block_count(), victim_data_length)
                if batch_size == 0:
                    self.blockManager.print_status()
                    raise Exception("WARNING!!! No free GC/WL block available for GC!")
                batch_blockIDs = victimDataBlockIDs[startIndex:startIndex + batch_size]
                startIndex    += batch_size
                victim_data_length -= batch_size
                migrated_data_pages += self.GC_writes(batch_blockIDs, BlockType.DATA, time.time_ns())
            self.blockManager.erases(victimDataBlockIDs, BlockType.DATA)

        victim_translation_length = len(victimTranslationBlockIDs)
        if victim_translation_length >= self.blockManager.total_planes: # due to RR allocation
            isGCTranslationRun = True
            startIndex = 0
            while victim_translation_length > 0:
                batch_size     = min(self.blockManager.get_gc_wl_free_block_count(), victim_translation_length)
                if batch_size == 0:
                    raise Exception("WARNING!!! No free GC/WL block available for GC translation page migration!")
                batch_blockIDs = victimTranslationBlockIDs[startIndex:startIndex + batch_size]
                startIndex    += batch_size
                victim_translation_length -= batch_size
                migrated_translation_pages += self.GC_writes(batch_blockIDs, BlockType.TRANSLATION, time.time_ns())
            self.blockManager.erases(victimTranslationBlockIDs, BlockType.TRANSLATION)

        if isGCDataRun and isGCTranslationRun:
            self.GC_runtime += 1
            self.GC_tracker[self.GC_runtime] = victimBlockIDs
            self.statistics.track_gc_run(len(victimBlockIDs), migrated_data_pages, migrated_translation_pages)
        elif isGCDataRun:
            self.GC_runtime += 1
            self.GC_tracker[self.GC_runtime] = victimDataBlockIDs
            self.statistics.track_gc_run(len(victimDataBlockIDs), migrated_data_pages, 0)
        elif isGCTranslationRun:
            self.GC_runtime += 1
            self.GC_tracker[self.GC_runtime] = victimTranslationBlockIDs
            self.statistics.track_gc_run(len(victimTranslationBlockIDs), 0, migrated_translation_pages)
        
        return victimDataBlockIDs

    def WL_run(self, highestErasedBlockID: int): #only support data block
        coldestDataBlockID   = self.WL_select_victim_coldestData_blockID()

        if self.blockManager.blockList[highestErasedBlockID].nErase - self.blockManager.blockList[coldestDataBlockID].nErase < self.wl_threshold:
            return

        #print("[WL] Start wear leveling...")
        #print(f"[WL] Highest Erased BlockID: {highestErasedBlockID}, Erase Count: {self.blockManager.blockList[highestErasedBlockID].nErase}")
        #print(f"[WL] Coldest Data BlockID: {coldestDataBlockID}, Last Write Time: {self.blockManager.blockList[coldestDataBlockID].last_write_time}")
        migrated_pages = self.WL_relocate(coldestDataBlockID, highestErasedBlockID)
        self.blockManager.erases([coldestDataBlockID], BlockType.DATA)

        #print("[WL] Complete wear leveling!")
        self.WL_runtime += 1
        self.WL_highestEraseBlockID_tracker[self.WL_runtime] = highestErasedBlockID
        self.WL_coldestDataBlockID_tracker[self.WL_runtime]  = coldestDataBlockID
        self.statistics.track_wl_run(migrated_pages)

    # WL: Victim Coldest-Data Block Selection Policy: give a block list, choose a block with the earliest write time
    def WL_select_victim_coldestData_blockID(self) -> int:
        return min((i for i, block in enumerate(self.blockManager.blockList) if ((block.blockType == BlockType.DATA) and (block.blockState != BlockState.FREE))), key=lambda i: self.blockManager.blockList[i].last_write_time)

    # WL: Coldest-Data block migration
    def WL_relocate(self, sourceBlockID: int, destinationBlockID: int) -> int:
        timestamp = self.blockManager.blockList[sourceBlockID].last_write_time

        assert(self.blockManager.blockList[sourceBlockID].blockType == BlockType.DATA)
        #assert(self.blockManager.blockList[destinationBlockID].blockType == BlockType.DATA)

        if self.blockManager.blockList[destinationBlockID].blockState != BlockState.FREE:
            raise Exception("[ERROR] Relocating to a non-free block!")

        validPageIndexes = self.blockManager.get_valid_pageIndexes_from_blockID(sourceBlockID)

        validPageList: list[FlashPage] = []
        for pageIndex in validPageIndexes:
            old_ppa  = self.blockManager.get_ppa_from_blockID(sourceBlockID, pageIndex)
            page     = self.blockManager.read(old_ppa)
            validPageList.append(page)

        length = len(validPageList)
        if length > 0:
            self.WL_relocate_data_pages_to_target_blockID(destinationBlockID, validPageList, timestamp)
            return length
        return 0

    def WL_relocate_data_pages_to_target_blockID(self, destinationBlockID: int, validPageList: list[FlashPage], timestamp: int):
        validPageList.sort(key=lambda page: page.lpa_or_TPIndex)
        rangeList = self.build_lpa_ranges(validPageList)
        startIndex = 0
        for (lpa, rangeSize) in rangeList:
            self.operationEngine.relocate_data_pages_to_target_blockID(lpa, rangeSize,  destinationBlockID, validPageList[startIndex : startIndex + rangeSize], timestamp)
            startIndex += rangeSize

    # GC: Victim Block Selection Policy: Greedy (give a full data/translation block list, choose the least utilized block to clean)
    def GC_select_victim_blockIDs(self) -> list[int]:
        candidateIDs   = []
        victimBlockIDs = []
        nExpectedDataFreeBlock = self.blockManager.get_data_free_block_count()
        nExpectedTranslationFreeBlock = self.blockManager.get_translation_free_block_count()

        # Collect data and translation blocks that have invalid pages as GC candidates
        # Include both FULL and OPEN blocks with invalid pages
        for blockID, block in enumerate(self.blockManager.blockList):
            if block.blockType == BlockType.DATA or block.blockType == BlockType.TRANSLATION:
                # Select blocks with invalid pages (candidates for GC)
                if (block.blockState == BlockState.FULL) and (block.nInvalidPage > 0.8 * self.blockManager.pages_per_block):
                    candidateIDs.append(blockID)
        if len(candidateIDs) == 0:
            return []

        # Sort by number of invalid pages (ascending) - select blocks with most invalid pages first
        candidateIDs.sort(key=lambda blockID: self.blockManager.blockList[blockID].nInvalidPage)
        while len(candidateIDs) > 0:
            blockID = candidateIDs.pop()
            # Always add blocks that are completely invalid
            if self.blockManager.blockList[blockID].nInvalidPage == self.blockManager.pages_per_block:
                victimBlockIDs.append(blockID)
                if self.blockManager.blockList[blockID].blockType == BlockType.DATA:
                    nExpectedDataFreeBlock += 1
                if self.blockManager.blockList[blockID].blockType == BlockType.TRANSLATION:
                    nExpectedTranslationFreeBlock += 1
            else:
                # For partially invalid blocks, check if we need more free blocks
                expectedDataFreeBlockRatio = nExpectedDataFreeBlock / self.blockManager.data_total_blocks
                expectedTranslationFreeBlockRatio = nExpectedTranslationFreeBlock / self.blockManager.translation_total_blocks
                if (expectedDataFreeBlockRatio >= 1 - self.gc_target) and (expectedTranslationFreeBlockRatio >= 1 - self.gc_target):
                    break
                else:
                    victimBlockIDs.append(blockID)
                    if self.blockManager.blockList[blockID].blockType == BlockType.DATA:
                        nExpectedDataFreeBlock += 1
                    if self.blockManager.blockList[blockID].blockType == BlockType.TRANSLATION:
                        nExpectedTranslationFreeBlock += 1

        return victimBlockIDs
        
    def GC_writes(self, victimBlockIDs: list[int], blockType: BlockType, timestamp: int) -> tuple[int, int]:
        validPageList:        list[FlashPage] = []

        for blockID in victimBlockIDs:           
            validPageIndexes = self.blockManager.get_valid_pageIndexes_from_blockID(blockID)
            for pageIndex in validPageIndexes:
                old_ppa  = self.blockManager.get_ppa_from_blockID(blockID, pageIndex)
                page     = self.blockManager.read(old_ppa)
                validPageList.append(page)

        if len(validPageList) > 0 and blockType == BlockType.DATA:
            self.write_data_pages(validPageList, timestamp)     
        if len(validPageList) > 0 and blockType == BlockType.TRANSLATION:
            self.operationEngine.write_translation_pages(validPageList, timestamp)
        return len(validPageList)

    def write_data_pages(self, validDataPageList: list[FlashPage], timestamp: int):
        validDataPageList.sort(key=lambda page: page.lpa_or_TPIndex)
        rangeList = self.build_lpa_ranges(validDataPageList)
        startIndex = 0
        for (lpa, rangeSize) in rangeList:
            self.operationEngine.write_data_pages(lpa, rangeSize, validDataPageList[startIndex : startIndex + rangeSize], timestamp)
            startIndex += rangeSize          

    def build_lpa_ranges(self, sortedPageList: list[FlashPage]) -> list[tuple[int, int]]:
        if len(sortedPageList) == 0:
            return []

        rangeList: list[tuple[int, int]] = []
        lpa = sortedPageList[0].lpa_or_TPIndex
        previous_lpa = lpa
        rangeSize = 1

        for currentPage in sortedPageList[1:]:
            if currentPage.lpa_or_TPIndex == previous_lpa + 1:
                rangeSize += 1
            else:
                rangeList.append((lpa, rangeSize))
                lpa = currentPage.lpa_or_TPIndex
                rangeSize = 1
            previous_lpa = currentPage.lpa_or_TPIndex
        rangeList.append((lpa, rangeSize))
        return rangeList

    def reads(self, victimBlockIDs: list[int]) -> list[FlashPage]:
        validPageList: list[FlashPage] = []
        for blockID in victimBlockIDs:
            validPageIndexes = self.blockManager.get_valid_pageIndexes_from_blockID(blockID)
            for pageIndex in validPageIndexes:
                old_ppa  = self.blockManager.get_ppa_from_blockID(blockID, pageIndex)
                page     = self.blockManager.read(old_ppa)
                validPageList.append(page)
        return validPageList

    def print_status(self):
        print("")
        print("======================================")
        print("=== Garbage Collector Status ===")
        print("======================================")
        print("")
        print(f"GC times: {self.GC_runtime}")
        if self.GC_runtime > 0:
            for (key, value) in self.GC_tracker.items():
                if len(value) > 0:
                    print(f"Time: {key}, victimBlockIDList: {value}")

        print("")
        print("======================================")
        print("=== Wear Leveler Status ===")
        print("======================================")
        print("")
        print(f"WL times: {self.WL_runtime}")
        if self.WL_runtime > 0:
            print("Highest Erase BlockID Tracker: ......................")
            for (key, value) in self.WL_highestEraseBlockID_tracker.items():
                print(f"Time: {key}, HighestEraseBlockID: {value}")
            print("\nColdest-Data BlockID Tracker: ......................")
            for (key, value) in self.WL_coldestDataBlockID_tracker.items():
                print(f"Time: {key}, ColdestDataBlockID: {value}")
