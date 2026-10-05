from __future__ import annotations
from dataclasses import dataclass
from enum import Enum, auto
from FlashController import FlashController
import math
import os
from SSDConfig import SSDConfig
from Statistics import Statistics
from TimingTracker import TimingTracker
from Utilities import Utilities

class AllocationPolicy(Enum):
    CWDP = auto()   #Round-robin

@dataclass
class BlockAddr:
    channel: int
    chip:    int
    die:     int
    plane:   int
    block:   int

class BlockState(Enum):
    FREE   = auto()
    OPEN   = auto()
    ACTIVE = auto()
    FULL   = auto()
    DEAD   = auto()

class BlockType(Enum):
    DATA        = auto()
    TRANSLATION = auto()
    GC_WL       = auto()

class PageState(Enum):
    FREE    = auto()
    VALID   = auto()
    INVALID = auto()

class PageType(Enum):
    DATA        = auto()
    TRANSLATION = auto()

class FlashPage:
    def __init__(self, data, lpa_or_TPIndex: int, pageType: PageType, nEntry: int):
        self.data           = data
        self.lpa_or_TPIndex = lpa_or_TPIndex     #OOB
        self.pageType       = pageType           #OOB onlydebug
        self.nEntry         = nEntry             #OOB number of mapping entry in case of translation page

    def display(self):
        if self.pageType == PageType.TRANSLATION:
            offset = 0
            for entry in self.data:
                if entry.dummy:
                    print(f"({offset}, N/A, {entry.ppa})", end=" ")
                    offset += entry.ppa
                else:
                    print(f"({offset}, {entry.ppa}, {entry.mappingSize})", end=" ")
                    offset += entry.mappingSize
            print("")
        else: #PageType.DATA
            print("data:", self.data)

    def updatePPA(self, offset: int, ppa: int):
        if offset >= self.nEntry:
            raise Exception("ERROR!!! Update a ghost entry!")
        self.data[offset].updatePPA(ppa)

    def getPPA(self, offset: int) -> int:
        if offset >= self.nEntry:
            return -1
        return self.data[offset].getPPA()


class FlashBlock:
    def __init__(self, blockAddr: BlockAddr, pages_per_block: int):
        self.blockAddr       = blockAddr
        self.blockState      = BlockState.FREE
        self.blockType       = BlockType.DATA
        self.pages_per_block = pages_per_block
        self.pageStates      = [PageState.FREE for _ in range(self.pages_per_block)]

        self.next_free_page = 0

        #Counters
        self.nErase       = 0
        self.nInvalidPage = 0

        self.last_write_time = 0

    def get_valid_pageIndexes(self):
        return [i for i, p in enumerate(self.pageStates) if p == PageState.VALID]

    def getFreePageCount(self):
        return self.pages_per_block - self.next_free_page

    def invalidate_pages(self, pageIndex: int, size: int):
        for i in range(size):
            if self.pageStates[pageIndex + i] != PageState.VALID:
                raise Exception(f"[ERROR] Only valid page can be invalidated!!! State: {self.pageStates[pageIndex + i]}")
            
            self.pageStates[pageIndex + i] = PageState.INVALID
            self.nInvalidPage += 1

    def active(self):
        if (self.blockState == BlockState.FREE) or (self.blockState == BlockState.OPEN):
            self.blockState = BlockState.ACTIVE
        else:
            raise Exception("[ERROR] Invalid block state transition!!!")

    def erase(self):
        for s in range(self.pages_per_block):
            self.pageStates[s] = PageState.FREE

        self.next_free_page  = 0
        self.blockState      = BlockState.FREE
        self.nErase         += 1
        self.nInvalidPage    = 0
        self.last_write_time = 0

    def try_to_allocate_pages(self, n_pages) -> tuple[int, int]:
        if self.blockState != BlockState.ACTIVE:
            raise Exception("ERROR!!! Pages can only be allocated from an active block!!!")

        # if self.getFreePageCount() == 0:
        #     return (-1, 0)

        startPageIndex = self.next_free_page
        allocatedSize  = min(n_pages, self.getFreePageCount())
        for i in range(allocatedSize):
            self.pageStates[startPageIndex + i] = PageState.VALID

        self.next_free_page += allocatedSize
        if self.next_free_page == self.pages_per_block:
            self.blockState = BlockState.FULL

        return (startPageIndex, allocatedSize)


class BlockManager:
    def __init__(self, config: SSDConfig, statistics: Statistics, timingTracker: TimingTracker):
        self.policy                     = AllocationPolicy.CWDP

        self.entry_size                 = config.ftp_mapping_entry_size
        self.page_size                  = config.page_size
        self.pages_per_block            = config.pages_per_block
        self.blocks_per_plane           = config.blocks_per_plane
        self.planes_per_die             = config.planes_per_die
        self.dies_per_chip              = config.dies_per_chip
        self.chips_per_channel          = config.chips_per_channel
        self.n_channel                  = config.n_channel
        self.gc_wl_reserved_ratio       = config.gc_wl_reserved_ratio
        self.translation_reserved_ratio = config.translation_reserved_ratio

        self.total_planes = (self.n_channel         *
                             self.chips_per_channel *
                             self.dies_per_chip     *
                             self.planes_per_die)

        self.total_blocks = self.total_planes * self.blocks_per_plane

        self.gc_wl_total_blocks       = math.ceil(self.total_blocks * self.gc_wl_reserved_ratio)
        self.translation_total_blocks = math.ceil(self.total_blocks * self.translation_reserved_ratio)
        self.data_total_blocks        = self.total_blocks - self.gc_wl_total_blocks - self.translation_total_blocks

        self.blockList_metadata                            = config.blockList_metadata
        self.data_free_blockID_per_plane_metadata          = config.data_free_blockID_per_plane_metadata
        self.translation_free_blockID_per_plane_metadata   = config.translation_free_blockID_per_plane_metadata
        self.gc_wl_free_blockID_per_plane_metadata         = config.gc_wl_free_blockID_per_plane_metadata
        self.active_data_blockID_per_plane_metadata        = config.active_data_blockID_per_plane_metadata
        self.active_translation_blockID_per_plane_metadata = config.active_translation_blockID_per_plane_metadata
        self.active_gc_wl_blockID_per_plane_metadata       = config.active_gc_wl_blockID_per_plane_metadata
        self.current_plane_metadata                        = config.current_plane_metadata

        self.used_gc_wl_blockID_set = set() # track the used GC/WL blocks to swap with data/translation blocks when complete GC/WL
        self.plane_gc_wl_usage = [0] * self.total_planes # track the number of valid pages in each plane for GC victim selection
        
        if (os.path.exists(self.blockList_metadata)                            and \
            os.path.exists(self.data_free_blockID_per_plane_metadata)          and \
            os.path.exists(self.translation_free_blockID_per_plane_metadata)   and \
            os.path.exists(self.gc_wl_free_blockID_per_plane_metadata)         and \
            os.path.exists(self.active_data_blockID_per_plane_metadata)        and \
            os.path.exists(self.active_translation_blockID_per_plane_metadata) and \
            os.path.exists(self.active_gc_wl_blockID_per_plane_metadata)       and \
            os.path.exists(self.current_plane_metadata)):
            
            self.blockList                            = Utilities.load_from_file_with_pickle(self.blockList_metadata)
            self.data_free_blockID_per_plane          = Utilities.load_from_file_with_pickle(self.data_free_blockID_per_plane_metadata)
            self.translation_free_blockID_per_plane   = Utilities.load_from_file_with_pickle(self.translation_free_blockID_per_plane_metadata)
            self.gc_wl_free_blockID_per_plane         = Utilities.load_from_file_with_pickle(self.gc_wl_free_blockID_per_plane_metadata)
            self.active_data_blockID_per_plane        = Utilities.load_from_file_with_pickle(self.active_data_blockID_per_plane_metadata)
            self.active_translation_blockID_per_plane = Utilities.load_from_file_with_pickle(self.active_translation_blockID_per_plane_metadata)
            self.active_gc_wl_blockID_per_plane       = Utilities.load_from_file_with_pickle(self.active_gc_wl_blockID_per_plane_metadata)
            (self.channel, self.chip, self.die, self.plane) = Utilities.load_from_file_with_pickle(self.current_plane_metadata)

        else:
            self.blockList: list[FlashBlock]          = [None for _ in range(self.total_blocks)]
            self.data_free_blockID_per_plane          = {}    # key: planeID, value: list of blockID
            self.translation_free_blockID_per_plane   = {}    # key: planeID, value: list of blockID
            self.gc_wl_free_blockID_per_plane         = {}    # key: planeID, value: list of blockID
            self.active_data_blockID_per_plane        = {}    # key: planeID, value: blockID
            self.active_translation_blockID_per_plane = {}    # key: planeID, value: blockID
            self.active_gc_wl_blockID_per_plane       = {}    # key: planeID, value: blockID

            # Select plane
            self.channel = 0
            self.chip    = 0
            self.die     = 0
            self.plane   = 0

            remainingGCWLFreeBlocks        = self.gc_wl_total_blocks
            remainingTranslationFreeBlocks = self.translation_total_blocks
            remainingDataFreeBlocks        = self.data_total_blocks

            for blockIndex in range(self.blocks_per_plane):
                for planeID in range(self.total_planes):
                    blockID   = planeID * self.blocks_per_plane + blockIndex
                    blockAddr = self.get_blockAddr_from_blockID(blockID)
                    block     = FlashBlock(blockAddr, self.pages_per_block)

                    if remainingGCWLFreeBlocks > 0:
                        block.blockType = BlockType.GC_WL
                        self.gc_wl_free_blockID_per_plane.setdefault(planeID, []).append(blockID)
                        remainingGCWLFreeBlocks -= 1
                    elif remainingTranslationFreeBlocks > 0:
                        block.blockType = BlockType.TRANSLATION
                        self.translation_free_blockID_per_plane.setdefault(planeID, []).append(blockID)
                        remainingTranslationFreeBlocks -= 1
                    elif remainingDataFreeBlocks > 0:
                        block.blockType = BlockType.DATA
                        self.data_free_blockID_per_plane.setdefault(planeID, []).append(blockID)
                        remainingDataFreeBlocks -= 1

                    self.blockList[blockID] = block

        self.nCurrentFreeDataBlocks        = self.count_free_blocks(BlockType.DATA)
        self.nCurrentFreeTranslationBlocks = self.count_free_blocks(BlockType.TRANSLATION)
        self.nCurrentFreeGCWLBlocks        = self.count_free_blocks(BlockType.GC_WL)

        self.flashController = FlashController(config, statistics, timingTracker)  

    def count_free_blocks(self, blockType: BlockType) -> int:
        return sum(1 for b in self.blockList if (b.blockState == BlockState.FREE and b.blockType == blockType))

    def get_planeID_from_blockID(self, blockID: int) -> int:
        return blockID // self.blocks_per_plane

    def select_planeID(self) -> int:
        if self.policy == AllocationPolicy.CWDP:
            return self.select_planeID_CWDP()
        else:
            raise Exception("ERROR!!! Currently, Only support CWDP")

    def select_planeID_UsageBased(self, excludedPlaneIDs: set[int]) -> int:

        # Select the usable plane with the least number of GC/WL pages written.
        candidatePlaneIDs = []
        for planeID in range(self.total_planes):
            if planeID in excludedPlaneIDs:
                continue

            activeBlockID = self.active_gc_wl_blockID_per_plane.get(planeID, -1)
            hasActiveFreePage = (
                activeBlockID != -1 and
                self.blockList[activeBlockID].blockState != BlockState.FULL and
                self.blockList[activeBlockID].getFreePageCount() > 0
            )
            hasFreeBlock = bool(self.gc_wl_free_blockID_per_plane.get(planeID))
            if hasActiveFreePage or hasFreeBlock:
                candidatePlaneIDs.append(planeID)

        if len(candidatePlaneIDs) == 0:
            return -1

        return min(candidatePlaneIDs, key=lambda p: self.plane_gc_wl_usage[p])

    def select_planeID_CWDP(self):
        channel = self.channel
        chip    = self.chip
        die     = self.die
        plane   = self.plane

        self.channel += 1
        if self.channel == self.n_channel:
            self.channel = 0
            self.chip   += 1
            if self.chip == self.chips_per_channel:
                self.chip  = 0
                self.die  += 1
                if self.die == self.dies_per_chip:
                    self.die    = 0
                    self.plane += 1
                    if self.plane == self.planes_per_die:
                        self.plane = 0

        return self.get_planeID_from_hierarchy(channel, chip, die, plane)

    def get_planeID_from_hierarchy(self, channel, chip, die, plane) -> int:
        planeID = channel
        planeID = planeID * self.chips_per_channel + chip
        planeID = planeID * self.dies_per_chip     + die
        planeID = planeID * self.planes_per_die    + plane
        return planeID

    def get_blockID_from_blockAddr(self, blockAddr: BlockAddr) -> int:
        blockID = blockAddr.channel
        blockID = blockID * self.chips_per_channel + blockAddr.chip
        blockID = blockID * self.dies_per_chip     + blockAddr.die
        blockID = blockID * self.planes_per_die    + blockAddr.plane
        blockID = blockID * self.blocks_per_plane  + blockAddr.block
        return blockID

    def get_blockAddr_from_blockID(self, blockID: int) -> BlockAddr:
        block     = blockID % self.blocks_per_plane
        blockID //= self.blocks_per_plane
        plane     = blockID % self.planes_per_die
        blockID //= self.planes_per_die
        die       = blockID % self.dies_per_chip
        blockID //= self.dies_per_chip
        chip      = blockID % self.chips_per_channel
        blockID //= self.chips_per_channel
        channel   = blockID
        return BlockAddr(channel, chip, die, plane, block)

    def get_ppa_from_blockID(self, blockID: int, pageIndex: int) -> int:
        return blockID * self.pages_per_block + pageIndex

    def get_data_free_block_ratio(self) -> float:
        nFreeBlock = self.get_data_free_block_count()
        return nFreeBlock / self.data_total_blocks

    def get_translation_free_block_ratio(self) -> float:
        nFreeBlock = self.get_translation_free_block_count()
        return nFreeBlock / self.translation_total_blocks

    def get_data_free_block_count(self) -> int:
        return self.nCurrentFreeDataBlocks

    def get_translation_free_block_count(self) -> int:
        return self.nCurrentFreeTranslationBlocks
    
    def get_gc_wl_free_block_count(self) -> int:
        return self.nCurrentFreeGCWLBlocks

    def open_new_active_block_per_plane(self, planeID: int, blockType: BlockType) -> int:
        if blockType == BlockType.DATA:
            free_blockID_per_plane = self.data_free_blockID_per_plane
        elif blockType == BlockType.TRANSLATION:
            free_blockID_per_plane = self.translation_free_blockID_per_plane
        elif blockType == BlockType.GC_WL:
            free_blockID_per_plane = self.gc_wl_free_blockID_per_plane
        else:
            raise Exception("Invalid block context")

        if not free_blockID_per_plane.get(planeID):
            return -1

        blockID = free_blockID_per_plane[planeID].pop()
        if blockType == BlockType.DATA:
            self.nCurrentFreeDataBlocks -= 1
        elif blockType == BlockType.TRANSLATION:
            self.nCurrentFreeTranslationBlocks -= 1
        elif blockType == BlockType.GC_WL:
            self.nCurrentFreeGCWLBlocks -= 1
        else:
            raise Exception("Invalid block context")
        self.blockList[blockID].active()

        if blockType == BlockType.DATA:
            self.active_data_blockID_per_plane[planeID] = blockID
        elif blockType == BlockType.TRANSLATION:
            self.active_translation_blockID_per_plane[planeID] = blockID
        elif blockType == BlockType.GC_WL:
            self.active_gc_wl_blockID_per_plane[planeID] = blockID
            self.used_gc_wl_blockID_set.add(blockID)
        else:
            raise Exception("Invalid block context")

        return blockID

    def read(self, ppa: int) -> FlashPage:
        return self.flashController.read(ppa)

    def peek(self, ppa: int) -> FlashPage:
        return self.flashController.peek(ppa)

    def write(self, ppa: int, page: FlashPage, timestamp: int):
        self.flashController.write(ppa, page)
        blockID = ppa // self.pages_per_block
        self.blockList[blockID].last_write_time = timestamp


    def erases(self, victimBlockIDList: list[int], blockType: BlockType):

        # Reset GC/WL usage tracking
        if len(self.used_gc_wl_blockID_set) > 0:
            for index in range(len(self.plane_gc_wl_usage)):
                self.plane_gc_wl_usage[index] = 0
        #nUsedGCWLBlocks = len(self.used_gc_wl_blockID_set)
        #assert(nUsedGCWLBlocks <= self.gc_wl_total_blocks)

        nDataBlocksToConvert        = sum(1 for b in victimBlockIDList if self.blockList[b].blockType == BlockType.DATA)
        nTranslationBlocksToConvert = sum(1 for b in victimBlockIDList if self.blockList[b].blockType == BlockType.TRANSLATION)
        assert((len(victimBlockIDList) == nDataBlocksToConvert) or (len(victimBlockIDList) == nTranslationBlocksToConvert )) # make sure all victim blocks are of the same type
        #assert(nUsedGCWLBlocks <= nDataBlocksToConvert + nTranslationBlocksToConvert) # make sure there are no GCWL blocks are wasted

        nConvertedDataBlocks        = 0
        nConvertedTranslationBlocks = 0

        # Convert used GC_WL blocks to DATA/TRANSLATION blocks
        while len(self.used_gc_wl_blockID_set) > 0:
            blockID = self.used_gc_wl_blockID_set.pop()
            planeID = self.get_planeID_from_blockID(blockID)
            
            # # Remove from GC_WL free list if it was OPEN
            # if blockID in self.gc_wl_free_blockID_per_plane.get(planeID, []):
            #     self.gc_wl_free_blockID_per_plane[planeID].remove(blockID)
            #     self.nCurrentFreeGCWLBlocks -= 1
            #     raise Exception("ERROR!!! A used GC/WL block can't be in the GC/WL free list!!!")

            if self.active_gc_wl_blockID_per_plane.get(planeID) == blockID:
                del self.active_gc_wl_blockID_per_plane[planeID]
                #self.blockList[blockID].blockState = BlockState.OPEN
                if self.blockList[blockID].getFreePageCount() > 0:
                    self.blockList[blockID].blockState = BlockState.OPEN
                else:
                    self.blockList[blockID].blockState = BlockState.FULL

            if blockType == BlockType.DATA:
                if (self.blockList[blockID].blockState == BlockState.OPEN):
                    self.data_free_blockID_per_plane.setdefault(planeID, []).insert(0, blockID)
                self.blockList[blockID].blockType = BlockType.DATA
                nDataBlocksToConvert -= 1
                nConvertedDataBlocks += 1
            elif blockType == BlockType.TRANSLATION:
                if (self.blockList[blockID].blockState == BlockState.OPEN):
                    self.translation_free_blockID_per_plane.setdefault(planeID, []).insert(0, blockID)
                self.blockList[blockID].blockType = BlockType.TRANSLATION
                nTranslationBlocksToConvert -= 1
                nConvertedTranslationBlocks += 1
            else:
                raise Exception("ERROR!!! Invalid block type!!!")

        # Convert victim DATA/TRANSLATION blocks to GC_WL blocks (to replace used GC_WL blocks)
        for blockID in victimBlockIDList:
            planeID = self.get_planeID_from_blockID(blockID)
            blockType = self.blockList[blockID].blockType
            
            self.blockList[blockID].erase()
            self.flashController.erase(blockID)

            # remove active block if it was this one
            if blockType == BlockType.DATA:
                if self.active_data_blockID_per_plane.get(planeID) == blockID:
                    del self.active_data_blockID_per_plane[planeID]
                #if nUsedGCWLBlocks > 0:
                if self.nCurrentFreeGCWLBlocks < self.gc_wl_total_blocks:
                    self.gc_wl_free_blockID_per_plane.setdefault(planeID, []).insert(0, blockID)
                    self.blockList[blockID].blockType = BlockType.GC_WL
                    self.nCurrentFreeGCWLBlocks += 1
                    #nUsedGCWLBlocks -= 1
                else:
                    self.data_free_blockID_per_plane.setdefault(planeID, []).insert(0, blockID)
                    self.nCurrentFreeDataBlocks += 1
            elif blockType == BlockType.TRANSLATION:
                if self.active_translation_blockID_per_plane.get(planeID) == blockID:
                    del self.active_translation_blockID_per_plane[planeID]
                if self.nCurrentFreeGCWLBlocks < self.gc_wl_total_blocks:
                    self.gc_wl_free_blockID_per_plane.setdefault(planeID, []).insert(0, blockID)
                    self.blockList[blockID].blockType = BlockType.GC_WL
                    self.nCurrentFreeGCWLBlocks += 1
                    #nUsedGCWLBlocks -= 1
                else:
                    self.translation_free_blockID_per_plane.setdefault(planeID, []).insert(0, blockID)
                    self.nCurrentFreeTranslationBlocks += 1
            else:
                raise Exception("[ERROR] Invalid block type!!!")

    def invalidate_pages(self, startPPA: int, size: int):
        blockID   = startPPA // self.pages_per_block
        pageIndex = startPPA % self.pages_per_block
        self.blockList[blockID].invalidate_pages(pageIndex, size)

    def get_valid_pageIndexes_from_blockID(self, blockID: int) -> list[int]:
        return self.blockList[blockID].get_valid_pageIndexes()

    def allocate_pages_from_target_blockID(self, n_pages: int, blockID: int) -> tuple[int, int]:
        blockType = self.blockList[blockID].blockType
        planeID = self.get_planeID_from_blockID(blockID)

        if blockType == BlockType.DATA:
            old_active_blockID = self.active_data_blockID_per_plane.get(planeID, -1)
            if old_active_blockID == -1:
                self.data_free_blockID_per_plane[planeID].remove(blockID)
                if self.blockList[blockID].blockState == BlockState.FREE:
                    self.nCurrentFreeDataBlocks -= 1
                self.active_data_blockID_per_plane[planeID] = blockID
                self.blockList[blockID].active()
            elif old_active_blockID != blockID:
                self.data_free_blockID_per_plane[planeID].remove(blockID)
                if self.blockList[blockID].blockState == BlockState.FREE:
                    self.nCurrentFreeDataBlocks -= 1
                self.active_data_blockID_per_plane[planeID] = blockID
                self.data_free_blockID_per_plane[planeID].append(old_active_blockID)
                self.blockList[old_active_blockID].blockState = BlockState.OPEN    
                self.blockList[blockID].active()
        elif blockType == BlockType.TRANSLATION:
            old_active_blockID = self.active_translation_blockID_per_plane.get(planeID, -1)
            if old_active_blockID == -1:
                self.translation_free_blockID_per_plane[planeID].remove(blockID)
                if self.blockList[blockID].blockState == BlockState.FREE:
                    self.nCurrentFreeTranslationBlocks -= 1
                self.active_translation_blockID_per_plane[planeID] = blockID
                self.blockList[blockID].active()
            elif old_active_blockID != blockID:
                self.translation_free_blockID_per_plane[planeID].remove(blockID)
                if self.blockList[blockID].blockState == BlockState.FREE:
                    self.nCurrentFreeTranslationBlocks -= 1
                self.active_translation_blockID_per_plane[planeID] = blockID
                self.translation_free_blockID_per_plane[planeID].append(old_active_blockID)
                self.blockList[old_active_blockID].blockState = BlockState.OPEN
                self.blockList[blockID].active()
        elif blockType == BlockType.GC_WL:
            self.used_gc_wl_blockID_set.add(blockID)
            old_active_blockID = self.active_gc_wl_blockID_per_plane.get(planeID, -1)
            if old_active_blockID == -1:
                self.gc_wl_free_blockID_per_plane[planeID].remove(blockID)
                self.nCurrentFreeGCWLBlocks -= 1
                self.active_gc_wl_blockID_per_plane[planeID] = blockID
                self.blockList[blockID].active()
            elif old_active_blockID != blockID:
                self.gc_wl_free_blockID_per_plane[planeID].remove(blockID)
                self.nCurrentFreeGCWLBlocks -= 1
                self.active_gc_wl_blockID_per_plane[planeID] = blockID
                self.gc_wl_free_blockID_per_plane[planeID].append(old_active_blockID)
                self.nCurrentFreeGCWLBlocks += 1
                self.blockList[old_active_blockID].blockState = BlockState.OPEN
                self.blockList[blockID].active()
        else:
            raise Exception("[ERROR] Invalid block type!!!")

        (startPageIndex, allocatedSize) = self.blockList[blockID].try_to_allocate_pages(n_pages)
        if allocatedSize > 0:
            startPPA = self.get_ppa_from_blockID(blockID, startPageIndex)
            return (startPPA, allocatedSize)
        else:
            return (-1, 0) # allocation failed, no free page in this block 

    def try_to_allocate_pages(self, n_pages: int, blockType: BlockType) -> tuple[int, int]:
        if blockType == BlockType.DATA:
            active_blockID_per_plane = self.active_data_blockID_per_plane
        elif blockType == BlockType.TRANSLATION:
            active_blockID_per_plane = self.active_translation_blockID_per_plane
        elif blockType == BlockType.GC_WL:
            active_blockID_per_plane = self.active_gc_wl_blockID_per_plane
        else:
            raise Exception("Invalid block context")
        
        tryTimes = 0
        triedPlaneIDs = set()
        while tryTimes < self.total_planes:
            planeID = -1
            if blockType == BlockType.GC_WL:
                # For GC/WL block allocation, select plane based on usage to maximize GC efficiency
                planeID = self.select_planeID_UsageBased(triedPlaneIDs)
            else:
                planeID = self.select_planeID()
            if planeID == -1:
                return (-1, 0)
            triedPlaneIDs.add(planeID)
            blockID = active_blockID_per_plane.get(planeID, -1)
            if (blockID == -1) or (self.blockList[blockID].blockState == BlockState.FULL):
                blockID = self.open_new_active_block_per_plane(planeID, blockType)
            if blockID != -1:
                startPageIndex, allocatedSize = self.blockList[blockID].try_to_allocate_pages(n_pages)
                if blockType == BlockType.GC_WL:
                    self.plane_gc_wl_usage[planeID] += allocatedSize
                if allocatedSize > 0:
                    startPPA = self.get_ppa_from_blockID(blockID, startPageIndex)
                    return (startPPA, allocatedSize)
            tryTimes += 1

        return (-1, 0) # allocation failed, no free page
   
    def dump_block_manager_data(self):
        Utilities.dump_to_file_with_pickle(self.blockList, self.blockList_metadata)
        Utilities.dump_to_file_with_pickle(self.data_free_blockID_per_plane, self.data_free_blockID_per_plane_metadata)
        Utilities.dump_to_file_with_pickle(self.translation_free_blockID_per_plane, self.translation_free_blockID_per_plane_metadata)
        Utilities.dump_to_file_with_pickle(self.gc_wl_free_blockID_per_plane, self.gc_wl_free_blockID_per_plane_metadata)
        Utilities.dump_to_file_with_pickle(self.active_data_blockID_per_plane, self.active_data_blockID_per_plane_metadata)
        Utilities.dump_to_file_with_pickle(self.active_translation_blockID_per_plane, self.active_translation_blockID_per_plane_metadata)
        Utilities.dump_to_file_with_pickle(self.active_gc_wl_blockID_per_plane, self.active_gc_wl_blockID_per_plane_metadata)
        Utilities.dump_to_file_with_pickle((self.channel, self.chip, self.die, self.plane), self.current_plane_metadata)

    def dump_flash_array(self):
        self.flashController.dump_flash_array()

    def print_status(self):
        #data blocks stats
        data_free   = sum(1 for b in self.blockList if (b.blockState == BlockState.FREE and b.blockType == BlockType.DATA))
        data_openb  = sum(1 for b in self.blockList if (b.blockState == BlockState.OPEN and b.blockType == BlockType.DATA))
        data_active = sum(1 for b in self.blockList if (b.blockState == BlockState.ACTIVE and b.blockType == BlockType.DATA))
        data_full   = sum(1 for b in self.blockList if (b.blockState == BlockState.FULL and b.blockType == BlockType.DATA))
        data_other  = self.data_total_blocks  - data_free - data_openb - data_active - data_full

        #translation blocks stats
        translation_free   = sum(1 for b in self.blockList if (b.blockState == BlockState.FREE and b.blockType == BlockType.TRANSLATION))
        translation_openb  = sum(1 for b in self.blockList if (b.blockState == BlockState.OPEN and b.blockType == BlockType.TRANSLATION))
        translation_active = sum(1 for b in self.blockList if (b.blockState == BlockState.ACTIVE and b.blockType == BlockType.TRANSLATION))
        translation_full   = sum(1 for b in self.blockList if (b.blockState == BlockState.FULL and b.blockType == BlockType.TRANSLATION))
        translation_other  = self.translation_total_blocks - translation_free - translation_openb - translation_active - translation_full

        #GC/WL blocks stats
        gc_wl_free   = sum(1 for b in self.blockList if (b.blockState == BlockState.FREE and b.blockType == BlockType.GC_WL))
        gc_wl_openb  = sum(1 for b in self.blockList if (b.blockState == BlockState.OPEN and b.blockType == BlockType.GC_WL))
        gc_wl_active = sum(1 for b in self.blockList if (b.blockState == BlockState.ACTIVE and b.blockType == BlockType.GC_WL))
        gc_wl_full   = sum(1 for b in self.blockList if (b.blockState == BlockState.FULL and b.blockType == BlockType.GC_WL))
        gc_wl_other  = self.gc_wl_total_blocks - gc_wl_free - gc_wl_openb - gc_wl_active - gc_wl_full

        print("=" * 60)
        print("Flash Block Manager Status")
        print("-" * 60)
        print(f"Data Total Blocks   : {self.data_total_blocks}")
        print(f"Data Free Blocks    : {data_free}")
        print(f"Data Open Blocks    : {data_openb}")
        print(f"Data Active Blocks  : {data_active}")
        print(f"Data Full Blocks    : {data_full}")
        if data_other != 0:
            print(f"Other Data Blocks   : {data_other}")
        print("-" * 60)
        print(f"Translation Total Blocks   : {self.translation_total_blocks}")
        print(f"Translation Free Blocks    : {translation_free}")
        print(f"Translation Open Blocks    : {translation_openb}")
        print(f"Translation Active Blocks  : {translation_active}")
        print(f"Translation Full Blocks    : {translation_full}")
        if translation_other != 0:
            print(f"Other Translation Blocks   : {translation_other}")
        print("-" * 60)
        print(f"GC/WL Total Blocks   : {self.gc_wl_total_blocks}")
        print(f"GC/WL Free Blocks    : {gc_wl_free}")
        print(f"GC/WL Open Blocks    : {gc_wl_openb}")
        print(f"GC/WL Active Blocks  : {gc_wl_active}")
        print(f"GC/WL Full Blocks    : {gc_wl_full}")
        if gc_wl_other != 0:
            print(f"Other GC/WL Blocks   : {gc_wl_other}")
        print("-" * 60)
