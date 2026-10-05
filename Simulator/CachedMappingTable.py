from SSDConfig import SSDConfig
from bisect import bisect_left, bisect_right
from collections import OrderedDict

class CachedMappingEntry:
    def __init__(self, offset: int, ppa: int, mappingSize: int = 1, dirty: bool = None):
        self.offset      = offset
        self.ppa         = ppa
        self.mappingSize = mappingSize
        self.dirty       = dirty
        self.timestamp   = 0

    def isMatchCME(self, offset: int) -> bool:
        if (self.offset <= offset) and (offset < self.offset + self.mappingSize):
            return True
        return False

    def getPPA(self, offset: int) -> int:
        return self.ppa + offset - self.offset

    def updatePPA(self, ppa: int):
        self.ppa   = ppa
        self.dirty = True

    def display(self):
        print(f"({self.offset}, {self.ppa}, {self.mappingSize})", end=" ")
        if  self.mappingSize <= 0:
            raise Exception("MappingSize <= 0")

    def getEndOffset(self) -> int:
        return self.offset + self.mappingSize - 1
    
    def getNextOffset(self) -> int:
        return self.offset + self.mappingSize
    
    def isDirty(self) -> bool:
        return self.dirty


class CachedTranslationPage:
    def __init__(self, TPIndex: int, mappingList: list[any] = None, mappingOffsets: list[int] = None, TPSize: int = 1, dirty: bool = None):
        self.TPIndex     = TPIndex
        self.mappingList = mappingList if mappingList is not None else []
        self.mappingOffsets = mappingOffsets if mappingOffsets is not None else []
        self.TPSize      = TPSize
        self.dirty       = dirty

    def updatePPA(self, offset: int, ppa: int):
        entry = self.mappingList[offset]
        entry.updatePPA(ppa)

    def getPPA(self, offset: int) -> int:
        entry = self.mappingList[offset]
        return entry.getPPA()

    def isMatchCTP(self, TPIndex: int) -> bool:
        if (self.TPIndex <= TPIndex) and (TPIndex < self.TPIndex + self.TPSize):
            return True
        return False

    def insertCME_CCFTL(self, entry: CachedMappingEntry):
        self.mappingList.insert(0, entry)

    def insertCME_rFTL(self, entry: CachedMappingEntry):
        idx = bisect_left(self.mappingOffsets, entry.offset)
        self.mappingList.insert(idx, entry)
        self.mappingOffsets.insert(idx, entry.offset)

    def getOverlapEntries_CCFTL(self, offset: int, size: int) -> list[CachedMappingEntry]:
        sortedMappingList = sorted(self.mappingList, key=lambda entry: entry.offset)
        overlapEntries = []
        end_req = offset + size
        for entry in sortedMappingList:
            start = entry.offset
            end   = entry.offset + entry.mappingSize
            if end <= offset:   # left mappings
                continue
            if end_req <= start:    # right mappings
                break
            overlapEntries.append(entry)
        return overlapEntries

    def getOverlapEntries_rFTL(self, offset: int, size: int) -> list[CachedMappingEntry]:
        overlapEntries = []
        end_req = offset + size
        idx = bisect_right(self.mappingOffsets, offset) - 1
        if idx < 0:
            idx = 0
        while idx < len(self.mappingList):
            entry = self.mappingList[idx]
            start = entry.offset
            end = entry.offset + entry.mappingSize
            if end_req <= start:
                break
            if end > offset:
                overlapEntries.append(entry)
            idx += 1
        return overlapEntries

    def removeCME_CCFTL(self, entry: CachedMappingEntry):
        self.mappingList.remove(entry)

    def removeCME_rFTL(self, entry: CachedMappingEntry):
        idx = self.mappingList.index(entry)
        self.mappingList.pop(idx)
        self.mappingOffsets.pop(idx)

    def popVictimCME(self) -> CachedMappingEntry:
        return self.mappingList.pop()

    def display(self):
        preOffset = 0
        preMappingSize = 0
        for entry in self.mappingList:
            entry.display()
            if(preOffset + preMappingSize > entry.offset):
                raise Exception("Overlap entry")
            preOffset = entry.offset
            preMappingSize = entry.mappingSize
        print("")


class CachedMappingTable:
    def __init__(self, ftlScheme, cfg: SSDConfig):
        self.ftlScheme = ftlScheme
        self.cache : list[CachedTranslationPage] = []
        #self.dftl_cmt : dict[int, CachedMappingEntry] = {}
        self.dftl_cmt : OrderedDict[int, CachedMappingEntry] = OrderedDict()
        self.cmt_capacity                        = cfg.cmt_capacity
        self.cmt_ctp_entry_size                  = cfg.cmt_ctp_entry_size
        self.cmt_mapping_entry_size              = cfg.cmt_mapping_entry_size
        self.entries_per_ftp                     = cfg.page_size // cfg.ftp_mapping_entry_size
        self.page_size                           = cfg.page_size
        self.timer                               = 0

    def getCME(self, lpa: int) -> CachedMappingEntry:
        targetCME = self.dftl_cmt.get(lpa)
        if targetCME:
            #self.mruUpdate_DFTL(targetCME)
            self.dftl_cmt.move_to_end(lpa) # update MRU O(1)
        return targetCME

    #def getCME(self, lpa: int) -> CachedMappingEntry:
    #    if self.ftlScheme == "DFTL":
    #        return self.getCME_DFTL(lpa)
    #    # elif self.ftlScheme == "CCFTL":
    #    #     return self.getCME_CCFTL(lpa)
    #    else:
    #        raise Exception("ERROR!!! Not support current FTL scheme!")

    # def getCME_CCFTL(self, offset: int) -> CachedMappingEntry:
    #     targetCME = None
    #     for cme in self.cache:
    #         if cme.isMatchCME(offset):
    #             targetCME = cme
    #             break
    #     if targetCME:
    #         self.mruUpdate_CCFTL_rFTL(targetCME)
    #         return targetCME
    #     return None

    #def getCME_DFTL(self, lpa: int) -> CachedMappingEntry:
    #    targetCME = self.dftl_cmt.get(lpa)
    #    if targetCME:
    #        self.mruUpdate_DFTL(targetCME)
    #    return targetCME

    def getCTP(self, TPIndex: int, update_mru: bool = False) -> CachedTranslationPage:
        targetCTP = None
        for ctp in self.cache:
            if ctp.isMatchCTP(TPIndex):
                targetCTP = ctp
                break
        if targetCTP and update_mru:
            self.mruUpdate_CCFTL_rFTL(targetCTP)
        return targetCTP

    def isCTPInsertionSafe(self, CTP: CachedTranslationPage) -> bool:
        if self.ftlScheme == "CDFTL":
            return self.isCTPInsertionSafe_CDFTL(CTP)
        newCTPSize = self.cmt_ctp_entry_size + (len(CTP.mappingList) * self.cmt_mapping_entry_size)
        currentSize = self.getCurrentSize()
        if currentSize + newCTPSize > self.cmt_capacity:
            return False
        return True

    def isCTPInsertionSafe_CDFTL(self, CTP: CachedTranslationPage) -> bool:
        newCTPSize = self.cmt_ctp_entry_size + self.page_size
        currentSize = self.getCurrentSize()
        if currentSize + newCTPSize > self.cmt_capacity:
            return False
        return True

    def insertCTP(self, CTP: CachedTranslationPage):
        self.cache.insert(0, CTP)

    def insertCME_DFTL_CDFTL(self, entry: CachedMappingEntry):
        #self.timer += 1
        #entry.timestamp = self.timer
        #self.dftl_cmt[entry.offset] = entry
        self.dftl_cmt[entry.offset] = entry
        self.dftl_cmt.move_to_end(entry.offset) # Push to end O(1)

    def insertCME_CCFTL_rFTL(self, TPIndex: int, entry: CachedMappingEntry):
        for ctp in self.cache:
            if ctp.isMatchCTP(TPIndex):
                ctp.insertCME_CCFTL(entry)

    def mruUpdate_DFTL(self, entry: CachedMappingEntry):
        self.timer += 1
        entry.timestamp = self.timer

    def mruUpdate_CCFTL_rFTL(self, ctp: CachedTranslationPage):
        self.cache.remove(ctp)
        self.cache.insert(0, ctp)

    def getCurrentSize(self) -> int:
        if self.ftlScheme == "DFTL":
            return self.getCurrentSize_DFTL()
        elif self.ftlScheme == "CDFTL":
            return self.getCurrentSize_CDFTL()
        elif self.ftlScheme == "CCFTL":
            return self.getCurrentSize_CCFTL_rFTL()
        elif self.ftlScheme == "rFTL":
            return self.getCurrentSize_CCFTL_rFTL()
        else:
            raise Exception("ERROR!!! Not support current FTL scheme!")

    def getCurrentSize_DFTL(self) -> int:
        return self.cmt_mapping_entry_size * len(self.dftl_cmt)

    def getCurrentSize_CDFTL(self) -> int:
        sum = self.cmt_mapping_entry_size * len(self.dftl_cmt)
        sum += (len(self.cache) * self.page_size)
        return sum

    def getCurrentSize_CCFTL_rFTL(self) -> int:
        sum = self.cmt_ctp_entry_size * len(self.cache)
        for ctp in self.cache:
            sum += len(ctp.mappingList) * self.cmt_mapping_entry_size
        return sum

    def isFull(self) -> bool:
        return self.getCurrentSize() >= self.cmt_capacity

    def popVictimCME(self) -> CachedMappingEntry:
        if self.ftlScheme == "DFTL":
            return self.popVictimCME_DFTL_CDFTL()
        elif self.ftlScheme == "CDFTL":
            return self.popVictimCME_DFTL_CDFTL()
        elif self.ftlScheme == "CCFTL":
            return self.popVictimCME_CCFTL()
        else:
            raise Exception("ERROR!!! Not support current FTL scheme!")

    def popVictimCME_DFTL_CDFTL(self) -> CachedMappingEntry:
        if len(self.dftl_cmt) == 0:
            return None
        ## find entry with the smallest timestamp (LRU)
        #victimCME = min(self.dftl_cmt.values(), key=lambda x: x.timestamp)
        #del self.dftl_cmt[victimCME.offset]

        #remove the LRU element O(1)
        _, victimCME = self.dftl_cmt.popitem(last=False)

        return victimCME

    def popVictimCME_CCFTL(self) -> CachedMappingEntry:
        victimCTP = self.getVictimCTP()
        victimCME = victimCTP.popVictimCME()
        if len(victimCTP.mappingList) == 0:
            self.cache.pop()
        return victimCME

    def popCME(self, lpa: int) -> CachedMappingEntry:
        cme = self.dftl_cmt.pop(lpa)
        return cme

    def updatePPA(self, lpa: int, newPPA: int):
        self.timer += 1
        cme = self.dftl_cmt[lpa]
        cme.updatePPA(newPPA)
        cme.timestamp = self.timer
        self.dftl_cmt.move_to_end(lpa)

    def getVictimCTP(self) -> CachedTranslationPage:
        return self.cache[-1]
    
    def popVictimCTP(self) -> CachedTranslationPage:
        return self.cache.pop()

    def popMruCTP(self) -> CachedTranslationPage:
        return self.cache.pop(0)

    def getRedundantCMEs(self) -> tuple[dict, int]:
        redundantCMEs : dict[int, list[int]] = {}  # TPIndex -> list of lpa
        count : int = 0
        for lpa in self.dftl_cmt.keys():
            TPIndex = lpa // self.entries_per_ftp
            CTP = self.getCTP(TPIndex)
            if CTP:
                if TPIndex not in redundantCMEs:
                    redundantCMEs[TPIndex] = []
                redundantCMEs[TPIndex].append(lpa)
                count += 1
        return (redundantCMEs, count)

    def print_status(self, detail : bool = False):
        print("************ CMT status **************")
        print(f"CMT capacity: {self.cmt_capacity} bytes")
        print(f"CMT size: {self.getCurrentSize()} bytes")
        
        if self.ftlScheme == "DFTL":
            for cme in self.dftl_cmt.values():
                 print(f"CME(offset, ppa, dirty, ts) = ({cme.offset}, {cme.ppa}, {cme.dirty}, {cme.timestamp})")
        else:
            for ctp in self.cache:
                print(f"CTP(TPIndex, NumEntries, TPSize, dirty) = ({ctp.TPIndex}, {len(ctp.mappingList)}, {ctp.TPSize}, {ctp.dirty})")
                if detail:
                    ctp.display()

