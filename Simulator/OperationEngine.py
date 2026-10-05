from BlockManager import BlockManager, BlockType, FlashPage, PageType
from MappingManager import MappingManager
from SSDConfig import SSDConfig
from Statistics import Statistics
import math

class OperationEngine:
    def __init__(self, cfg: SSDConfig, blockManager: BlockManager, mappingManager: MappingManager, statistics: Statistics):
        self.blockManager    = blockManager
        self.mappingManager  = mappingManager
        self.entries_per_ftp = cfg.page_size // cfg.ftp_mapping_entry_size
        self.statistics      = statistics
        self.page_size       = cfg.page_size
        self.total_pages     = (cfg.n_channel         * 
                                cfg.chips_per_channel * 
                                cfg.dies_per_chip     * 
                                cfg.planes_per_die    * 
                                cfg.blocks_per_plane  * 
                                cfg.pages_per_block)
        # Flash Mapping Entry format:  | startPPA (x bits) | mappingSize (y bits) | dummy flag (1 bit) |
        ppa_width = math.ceil(math.log2(self.total_pages))
        self.max_mapping_size = 2 ** (32 - ppa_width - 1) # 1 bit for dummy flag

    def writes_data_using_lpa(self, startLPA: int, requestSize: int, dataList: list[any], timestamp: int):
        startIndex     = 0
        subRequests = self.split_request(startLPA, requestSize)
        for (lpa, size) in subRequests:
            current_lpa    = lpa
            remaining_size = size
            while remaining_size > 0:
                # Allocate a range
                (newStartPPA, newMappingSize) = self.blockManager.try_to_allocate_pages(remaining_size, BlockType.DATA)
                if newMappingSize == 0:
                    print("WARNING! No more free pages available for allocation!")
                    return False
                    #self.blockManager.print_status()
                    #quit()
                # Update mapping
                self.mappingManager.update_data_mapping(current_lpa, newStartPPA, newMappingSize)
                self.statistics.track_operation_host_data_write(newMappingSize)
                # write data
                for offset in range(newMappingSize):
                    ftp = FlashPage(
                        data           = dataList[startIndex + offset],
                        lpa_or_TPIndex = current_lpa + offset,
                        pageType       = PageType.DATA,
                        nEntry         = self.entries_per_ftp
                    )
                    self.blockManager.write(newStartPPA + offset, ftp, timestamp)
                startIndex     += newMappingSize
                current_lpa    += newMappingSize
                remaining_size -= newMappingSize
        return True

    def write_data_pages(self, startLPA: int, requestSize: int, pageList: list[FlashPage], timestamp: int):
        startIndex  = 0
        subRequests = self.split_request(startLPA, requestSize)
        for (lpa, size) in subRequests:
            current_lpa    = lpa
            remaining_size = size
            while remaining_size > 0:
                # Allocate a new page range
                (newStartPPA, newMappingSize) = self.blockManager.try_to_allocate_pages(remaining_size, BlockType.GC_WL)
                if newMappingSize == 0:
                    print("WARNING! No more free pages during GC!")
                    self.blockManager.print_status()
                    quit()
                # Update mapping
                self.mappingManager.update_data_mapping(current_lpa, newStartPPA, newMappingSize)
                self.statistics.track_operation_gc_data_write(newMappingSize)
                # write a page range
                for offset in range(newMappingSize):
                    self.blockManager.write(newStartPPA + offset, pageList[startIndex + offset], timestamp)
                startIndex     += newMappingSize
                current_lpa    += newMappingSize
                remaining_size -= newMappingSize

    def write_translation_pages(self, pageList: list[FlashPage], timestamp: int):
        for page in pageList:
            (newPPA, _) = self.blockManager.try_to_allocate_pages(1, BlockType.GC_WL)
            if newPPA == -1:
                print("WARNING! No more free pages during GC!")
                self.blockManager.print_status()
                quit()
            self.statistics.track_operation_translation_write()
            self.blockManager.write(newPPA, page, timestamp)
            self.mappingManager.update_translation_mapping(page.lpa_or_TPIndex, newPPA)

    def relocate_data_pages_to_target_blockID(self, startLPA: int, requestSize: int, destinationBlockID: int, pageList: list[FlashPage], timestamp: int):
        subRequests = self.split_request(startLPA, requestSize)
        startIndex  = 0
        for (lpa, size) in subRequests:
            current_lpa    = lpa
            remaining_size = size
            while remaining_size > 0:
                # Allocate a new page range
                (newStartPPA, newMappingSize) = self.blockManager.allocate_pages_from_target_blockID(remaining_size, destinationBlockID)
                if newMappingSize == 0:
                    print("WARNING! No more free pages in destination block during WL!")
                    self.blockManager.print_status()
                    quit()
                # Update mapping
                self.mappingManager.update_data_mapping(current_lpa, newStartPPA, newMappingSize)
                self.statistics.track_operation_wl_data_write(newMappingSize)
                # write a page range
                for offset in range(newMappingSize):
                    self.blockManager.write(newStartPPA + offset, pageList[startIndex + offset], timestamp)
                startIndex     += newMappingSize
                current_lpa    += newMappingSize
                remaining_size -= newMappingSize

    def relocate_translation_pages_to_target_blockID(self, destinationBlockID: int, pageList: list[FlashPage], timestamp: int):
        for page in pageList:
            (newPPA, newMappingSize) = self.blockManager.allocate_pages_from_target_blockID(1, destinationBlockID)
            if newMappingSize == 0:
                print("WARNING! No more free pages in destination block during WL!")
                self.blockManager.print_status()
                quit()
            self.mappingManager.update_translation_mapping(page.lpa_or_TPIndex, newPPA)
            self.statistics.track_operation_translation_relocation()
            self.statistics.track_operation_translation_write()
            self.blockManager.write(newPPA, page, timestamp)
    
    def reads_data_using_lpa(self, startLPA: int, requestSize: int) -> list[any]: 
        dataList: list[any] = []
        subRequests = self.split_request(startLPA, requestSize)
        for (lpa, size) in subRequests:
            ppa_ranges = self.mappingManager.lookup_reads(lpa, size)
            for (startPPA, mappingSize) in ppa_ranges:
                self.statistics.track_operation_data_read(mappingSize)
                for offset in range(mappingSize):
                    if startPPA == -1:
                        #dataList.append([0 for _ in range(self.page_size)])
                        raise Exception("ERROR!!! Unmapped LPA is read!")
                    else:
                        ftp = self.blockManager.read(startPPA + offset)
                        data = ftp.data
                        dataList.append(data)
        return dataList
    
    def read_page_using_ppa(self, ppa: int) -> FlashPage:
        return self.blockManager.read(ppa)

    def split_request(self, startLPA: int, requestSize: int) -> list[tuple[int, int]]:
        subRequests: list[tuple[int, int]] = []
        currentLPA    = startLPA
        remainingSize = requestSize
        while remainingSize > 0:
            offset          = currentLPA % self.entries_per_ftp
            remainingInPage = self.entries_per_ftp - offset
            chunkSize = min(remainingSize, self.max_mapping_size, remainingInPage)
            subRequests.append((currentLPA, chunkSize))
            currentLPA    += chunkSize
            remainingSize -= chunkSize
        return subRequests
