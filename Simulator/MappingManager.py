from BlockManager import BlockManager, BlockType, FlashPage, PageType
from GlobalTranslationDirectory import GlobalTranslationDirectory, GlobalTranslationEntry
from CachedMappingTable import CachedMappingTable, CachedTranslationPage, CachedMappingEntry
from SSDConfig import SSDConfig
from Statistics import Statistics
from TimingTracker import TimingTracker
import time

class FlashMappingEntry:
    def __init__(self, ppa: int = 1, valid: bool = False, mappingSize: int = 0):
        self.ppa         = ppa
        self.valid       = valid
        self.mappingSize = mappingSize

    def updatePPA(self, ppa: int):
        self.ppa   = ppa
        self.valid = True

    def isValid(self) -> bool:
        return self.valid

    def getPPA(self) -> int:
        if self.valid:
            return self.ppa
        return -1


class MappingManager:
    def __init__(self, ftlScheme, cfg: SSDConfig, blockManager: BlockManager, statistics: Statistics, timingTracker: TimingTracker):
        self.FTLScheme = ftlScheme

        self.blockManager = blockManager
        self.statistics = statistics
        self.timingTracker = timingTracker

        self.CMT = CachedMappingTable(self.FTLScheme, cfg)
        self.GTD = GlobalTranslationDirectory(cfg)

        self.entries_per_ftp = cfg.page_size // cfg.ftp_mapping_entry_size
        self.rGTD_GROUP = cfg.rGTD_GROUP
        #self.maxTPSize = 16

    def lookup_reads(self, lpa: int, size: int) -> list[tuple[int, int]]:
        if self.FTLScheme == "DFTL":
            return self.lookup_reads_DFTL(lpa, size)
        elif self.FTLScheme == "CDFTL":
            return self.lookup_reads_CDFTL(lpa, size)
        elif self.FTLScheme == "CCFTL":
            return self.lookup_reads_CCFTL(lpa, size)
        elif self.FTLScheme == "rFTL":
            return self.lookup_reads_rFTL(lpa, size)
        else:
            raise Exception("ERROR!!! Not support current FTL scheme!")

    def update_data_mapping(self, lpa: int, newPPA: int, size: int):
        if self.FTLScheme == "DFTL":
            self.update_data_mapping_DFTL(lpa, newPPA, size)
        elif self.FTLScheme == "CDFTL":
            self.update_data_mapping_CDFTL(lpa, newPPA, size)
        elif self.FTLScheme == "CCFTL":
            self.update_data_mapping_CCFTL(lpa, newPPA, size)
        elif self.FTLScheme == "rFTL":
            self.update_data_mapping_rFTL(lpa, newPPA, size)
        else:
            raise Exception("ERROR!!! Not support current FTL scheme!")

    def lookup_reads_CDFTL(self, lpa: int, size: int) -> list[tuple[int, int]]:
        TPIndex = lpa // self.entries_per_ftp
        offset  = lpa % self.entries_per_ftp
        ppas : list[tuple[int, int]] = []

        for i in range(size):
            cme = self.getCME(lpa + i)
            if not cme:
                GTE = self.getGTE(TPIndex)
                if not GTE:
                    print("ERROR!!! GTD is faulty!")
                if GTE.isLocatedInCache():
                    self.statistics.track_cmt_hit_by_page(1)
                    CTP = self.getCTP(TPIndex, update_mru=True)
                    if not CTP:
                        print("ERROR!!! GTD is faulty! No CTP found for TPIndex {TPIndex} in cache")
                        quit()
                    ppa = CTP.getPPA(offset + i)
                    if ppa == -1:
                        print("ERROR!!! Read a page that hasn't been written yet!")
                        quit()
                    cme = CachedMappingEntry(
                        offset = lpa + i,
                        ppa    = ppa,
                        dirty  = False
                    )
                    self.insertCME_CDFTL(cme)
                    ppas.append((ppa, 1))
                elif GTE.isLocatedInFlash():
                    self.statistics.track_cmt_miss_by_page(1)
                    ftp = self.blockManager.read(GTE.ppa)
                    if not ftp:
                        print("ERROR!!! GTD is faulty! No FTP found for TPIndex {TPIndex} at PPA {gte.ppa}")
                        quit()
                    CTP = CachedTranslationPage(
                        TPIndex      = TPIndex,
                        mappingList  = ftp.data
                    )
                    ppa = CTP.getPPA(offset + i)
                    cme = CachedMappingEntry(
                        offset = lpa + i,
                        ppa    = ppa,
                        dirty  = False
                    )
                    self.insertCME_CDFTL(cme)
                    self.insertCTP_CDFTL(CTP)
                    GTE.moveToCache()
                    ppas.append((ppa, 1))
                else:
                    raise Exception("ERROR!!! GTD is faulty! GTE for TPIndex {TPIndex} has invalid location")
                    
            else: # CMT hit
                self.statistics.track_cmt_hit_by_page(1)
                ppas.append((cme.ppa, 1))
        return ppas

    def lookup_reads_DFTL(self, lpa: int, size: int) -> list[tuple[int, int]]:
        TPIndex       = lpa // self.entries_per_ftp
        offset_start  = lpa  % self.entries_per_ftp
        ppas : list[tuple[int, int]] = []

        ftp = None
        for i in range(size):
            cme = self.getCME(lpa + i)
            if not cme:
                self.statistics.track_cmt_miss_by_page(1)
                GTE = self.getGTE(TPIndex)
                if not GTE or GTE.ppa == -1:
                    raise Exception("ERROR!!! Read a page that hasn't been written yet!")
                if not ftp:
                    ftp = self.blockManager.read(GTE.ppa)
                ppa = ftp.getPPA(offset_start + i)
                cme = CachedMappingEntry(
                    offset = lpa + i,
                    ppa    = ppa,
                    dirty  = False
                )
                self.insertCME_DFTL(cme)
                ppas.append((ppa, 1))
            else:
                self.statistics.track_cmt_hit_by_page(1)
                ppas.append((cme.ppa, 1))
        return ppas

    def lookup_reads_CCFTL(self, lpa: int, size: int) -> list[tuple[int, int]]:
        TPIndex      = lpa // self.entries_per_ftp
        offset_start = lpa % self.entries_per_ftp
        offset_next  = offset_start + size

        CTP = self.getCTP(TPIndex, update_mru=True)
        if not CTP:
            self.statistics.track_cmt_miss_by_page(1)
            self.statistics.track_cmt_hit_by_page(size - 1)
            GTE = self.getGTE(TPIndex)
            if not GTE or GTE.ppa == -1:
                raise Exception("ERROR!!! GTD is corrupt!")
            
            ftp = self.blockManager.read(GTE.ppa)
            ppa_list : list[int] = []
            for offset in range(offset_start, offset_next):
                ppa = ftp.getPPA(offset)
                if ppa == -1:
                    raise Exception("ERROR!!! Read a ghost page!")
                ppa_list.append(ppa)
            
            ppa_ranges = self.build_ppa_ranges(ppa_list)
            offset = offset_start
            cmeList: list[CachedMappingEntry] = []
            for startPPA, mappingSize in ppa_ranges:
                cme = CachedMappingEntry(
                    offset      = offset,
                    ppa         = startPPA,
                    mappingSize = mappingSize,
                    dirty       = False
                )
                offset += mappingSize
                cmeList.append(cme)

            CTP = CachedTranslationPage(
                TPIndex     = TPIndex,
                mappingList = cmeList
            )
            self.insertCTP_CCFTL(CTP)

            return ppa_ranges

        # Level-1 CMT hit
        overlapEntries = CTP.getOverlapEntries_CCFTL(offset_start, size)
        ppa_ranges : list[tuple[int, int]] = []
        current_offset = offset_start
        remainingSize = size
        ftp = None
        for overlapEntry in overlapEntries:
            if current_offset < overlapEntry.offset:
                ppa_list = []
                if not ftp:
                    GTE = self.getGTE(TPIndex)
                    if not GTE or GTE.ppa == -1:
                        raise Exception("ERROR!!! GTD is corrupt!")
                    ftp = self.blockManager.read(GTE.ppa)

                for offset in range(current_offset, overlapEntry.offset):
                    ppa = ftp.getPPA(offset)
                    if ppa == -1:
                        raise Exception("ERROR!!! Read a ghost page!")
                    ppa_list.append(ppa)

                offset = current_offset
                ppa_ranges_temp = self.build_ppa_ranges(ppa_list)
                ppa_ranges.extend(ppa_ranges_temp)
                for startPPA, mappingSize in ppa_ranges_temp:
                    cme = CachedMappingEntry(
                        offset      = offset,
                        ppa         = startPPA,
                        mappingSize = mappingSize,
                        dirty       = False
                    )
                    offset += mappingSize
                    self.insertCME_CCFTL(TPIndex, cme)
                remainingSize  -= (overlapEntry.offset - current_offset)
                self.statistics.track_cmt_miss_by_page(overlapEntry.offset - current_offset)
                current_offset  = overlapEntry.offset

            overlapEntry_next = overlapEntry.offset + overlapEntry.mappingSize
            startPPA    = overlapEntry.ppa + (current_offset - overlapEntry.offset)
            hitSize = min(remainingSize, overlapEntry_next - current_offset)
            ppa_ranges.append((startPPA, hitSize))
            remainingSize  -= hitSize
            current_offset += hitSize
            self.statistics.track_cmt_hit_by_page(hitSize)
            
        if remainingSize > 0:
            if not ftp:
                GTE = self.getGTE(TPIndex)
                if not GTE or GTE.ppa == -1:
                    raise Exception("ERROR!!! GTD is corrupt!")
                ftp = self.blockManager.read(GTE.ppa)
            
            ppa_list = []
            for offset in range(current_offset, offset_next):
                ppa = ftp.getPPA(offset)
                if ppa == -1:
                    raise Exception("ERROR!!! Read a ghost page!")
                ppa_list.append(ppa)
            ppa_ranges_temp = self.build_ppa_ranges(ppa_list)
            ppa_ranges.extend(ppa_ranges_temp)
            for startPPA, mappingSize in ppa_ranges_temp:
                    cme = CachedMappingEntry(
                        offset      = current_offset,
                        ppa         = startPPA,
                        mappingSize = mappingSize,
                        dirty       = False
                    )
                    current_offset += mappingSize
                    self.insertCME_CCFTL(TPIndex, cme)
            self.statistics.track_cmt_miss_by_page(1)
            self.statistics.track_cmt_hit_by_page(remainingSize - 1)

        # update MRU
        for entry in overlapEntries:
            CTP.removeCME_CCFTL(entry)
            CTP.insertCME_CCFTL(entry)

        return ppa_ranges

    def lookup_reads_rFTL(self, lpa: int, size: int) -> list[tuple[int, int]]:
        TPIndex      = lpa // self.entries_per_ftp

        CTP = self.getCTP(TPIndex, update_mru=True)
        if not CTP:
            self.statistics.track_cmt_miss_by_page(1)
            self.statistics.track_cmt_hit_by_page(size - 1)
        else:
            self.statistics.track_cmt_hit_by_page(size)

        if not CTP:
            GTE = self.getGTE(TPIndex)
            if not GTE or GTE.ppa == -1:
                raise Exception("ERROR!!! GTD is corrupt!")
            
            ftp = self.blockManager.read(GTE.ppa)
            if not ftp:
                print("ERROR!!! GTD is faulty!")
                quit()

            cmeList, offsetList = self.build_cmeList_from_fmeList(ftp.data)

            CTP = CachedTranslationPage(
                TPIndex     = GTE.TPIndex,
                mappingList = cmeList,
                mappingOffsets = offsetList,
                TPSize      = GTE.TPSize,
                dirty       = False
            )
            self.insertCTP_rFTL(CTP)

        offset_start = lpa % self.entries_per_ftp + (TPIndex - CTP.TPIndex) * self.entries_per_ftp
        offset_next  = offset_start + size

        # Level-1 CMT hit
        overlapEntries = CTP.getOverlapEntries_rFTL(offset_start, size)
        ppa_ranges : list[tuple[int, int]] = []
        matchSize = 0
        for mapping in overlapEntries:
            mapping_start = mapping.offset
            mapping_next  = mapping.offset + mapping.mappingSize

            overlap_start = max(offset_start, mapping_start)
            overlap_end   = min(offset_next,   mapping_next)

            distance = overlap_start - mapping_start
            chunk    = overlap_end   - overlap_start

            start_ppa = mapping.ppa + distance           
            ppa_ranges.append((start_ppa, chunk))
            matchSize += chunk

        if matchSize != size:
            print("ERROR!!! Read a page that hasn't been written yet!")
            quit()

        return ppa_ranges

    def update_data_mapping_CDFTL(self, lpa: int, newPPA: int, size: int):
        TPIndex = lpa // self.entries_per_ftp
        offset  = lpa % self.entries_per_ftp

        GTE = self.getGTE(TPIndex)
        CTP = self.getCTP(TPIndex, update_mru=True)
        if CTP:
            assert(GTE.isLocatedInCache())
            self.statistics.track_cmt_hit_by_page(size)
            for i in range(size):
                ppa = CTP.getPPA(offset + i)
                if ppa != -1:
                    self.blockManager.invalidate_pages(ppa, 1)
                CTP.updatePPA(offset + i, newPPA + i)
                cme = self.getCME(lpa + i)
                if cme:
                    self.popCME(lpa + i)
        else:
            remain_distances : list[int] = []
            for i in range(size):
                cme = self.getCME(lpa + i)
                if cme:
                    self.statistics.track_cmt_hit_by_page(1)
                    self.blockManager.invalidate_pages(cme.ppa, 1)
                    cme.updatePPA(newPPA + i)
                else:
                    remain_distances.append(i)

            if len(remain_distances) > 0:
                if not GTE:
                    self.statistics.track_cmt_hit_by_page(len(remain_distances))
                    GTE = GlobalTranslationEntry(
                        TPIndex = TPIndex,
                        TPSize  = 1,
                        valid = True
                    )
                    self.insertGTE(GTE)
                    CTP = CachedTranslationPage(
                        TPIndex = TPIndex,
                        mappingList = [FlashMappingEntry() for _ in range(self.entries_per_ftp)]
                    )
                    self.insertCTP_CDFTL(CTP)
                    for i in remain_distances:
                        CTP.updatePPA(offset + i, newPPA + i)
                else:
                    self.statistics.track_cmt_miss_by_page(1)
                    self.statistics.track_cmt_hit_by_page(len(remain_distances) - 1)
                    assert(GTE.isLocatedInFlash())
                    ftp = self.blockManager.read(GTE.ppa)
                    if not ftp:
                        raise Exception("ERROR!!! GTE is corrupt!")
                    if CTP:
                        raise Exception("ERROR!!! Detecting a ghost CTP!")
                    CTP = CachedTranslationPage(
                        TPIndex     = TPIndex,
                        mappingList = ftp.data
                    )
                    self.insertCTP_CDFTL(CTP)
                    GTE.moveToCache()
                    for i in remain_distances:
                        ppa = CTP.getPPA(offset + i)
                        if ppa != -1:
                            self.blockManager.invalidate_pages(ppa, 1)
                        CTP.updatePPA(offset + i, newPPA + i)
        GTE.dirty = True

    def update_data_mapping_DFTL(self, lpa: int, newPPA: int, size: int):
        TPIndex = lpa // self.entries_per_ftp
        offset_start = lpa % self.entries_per_ftp

        GTE = self.getGTE(TPIndex)
        if not GTE:
            GTE = GlobalTranslationEntry(
                TPIndex = TPIndex,
                valid = True
            )
            self.insertGTE(GTE)

        ftp = None
        for i in range(size):
            cme = self.getCME(lpa + i)
            if not cme:
                self.statistics.track_cmt_miss_by_page(1)
                if GTE.ppa != -1:
                    if not ftp:
                        ftp = self.blockManager.read(GTE.ppa)
                    ppa = ftp.getPPA(offset_start + i)
                    if ppa != -1:
                        self.blockManager.invalidate_pages(ppa, 1)
                # insert new mapping
                cme = CachedMappingEntry(
                    offset = lpa + i,
                    ppa    = newPPA + i,
                    dirty  = True
                )
                self.insertCME_DFTL(cme)
            else:
                self.statistics.track_cmt_hit_by_page(1)
                self.blockManager.invalidate_pages(cme.ppa, 1)
                self.updatePPA(lpa + i, newPPA + i)

    def updatePPA(self, lpa: int, newPPA: int):
        self.timingTracker.record_dram_access(is_write=True)
        self.CMT.updatePPA(lpa, newPPA)

    def update_data_mapping_CCFTL(self, lpa: int, newPPA: int, size: int):
        TPIndex = lpa // self.entries_per_ftp
        offset_start = lpa % self.entries_per_ftp
        offset_next  = offset_start + size

        remainMappings  = []

        # 1. lookup TPIndex in CMT
        CTP = self.getCTP(TPIndex, update_mru=True)

        # 2. CMT doesn't contain TPIndex
        if not CTP:
            self.statistics.track_cmt_miss_by_page(1)
            self.statistics.track_cmt_hit_by_page(size - 1)
            GTE = self.getGTE(TPIndex)
            if not GTE:
                GTE = GlobalTranslationEntry(
                    TPIndex = TPIndex,
                    valid = True
                )
                self.insertGTE(GTE)
            else:
                ftp = self.blockManager.read(GTE.ppa)
                for offset in range(offset_start, offset_next):
                    ppa = ftp.getPPA(offset)
                    if ppa == -1:
                        continue
                    self.blockManager.invalidate_pages(ppa, 1)

            # insert new mapping
            cme = CachedMappingEntry(
                offset      = offset_start,
                ppa         = newPPA,
                mappingSize = size,
                dirty       = True
            )
            CTP = CachedTranslationPage(TPIndex, [cme])
            self.insertCTP_CCFTL(CTP)
            return

        # 3. CMT contains TPIndex
        overlapEntries = CTP.getOverlapEntries_CCFTL(offset_start, size)
        current_offset = offset_start
        remainingSize  = size
        ftp = None
        for overlapEntry in overlapEntries:
            invalidOffsetStart = overlapEntry.offset
            invalidOffsetNext  = overlapEntry.offset + overlapEntry.mappingSize

            if current_offset < overlapEntry.offset:
                if not ftp:
                    GTE = self.getGTE(TPIndex)
                    if GTE and GTE.ppa != -1:
                        ftp = self.blockManager.read(GTE.ppa)
                if ftp:
                    for offset in range(current_offset, overlapEntry.offset):
                        ppa = ftp.getPPA(offset)
                        if ppa == -1:
                            continue
                        self.blockManager.invalidate_pages(ppa, 1)
                current_offset = overlapEntry.offset
                remainingSize -= (overlapEntry.offset - current_offset)
                #self.statistics.track_cmt_miss_by_page(overlapEntry.offset - current_offset)

            if current_offset > overlapEntry.offset: # remain left mapping
                leftEntry = CachedMappingEntry(
                    offset      = overlapEntry.offset,
                    ppa         = overlapEntry.ppa,
                    mappingSize = current_offset - overlapEntry.offset,
                    dirty       = overlapEntry.dirty
                )
                remainMappings.append(leftEntry)
                invalidOffsetStart = current_offset
            
            if offset_next < overlapEntry.offset + overlapEntry.mappingSize: # remain right mapping
                rightEntry = CachedMappingEntry(
                    offset      = offset_next,
                    ppa         = overlapEntry.ppa + (offset_next - overlapEntry.offset),
                    mappingSize = (overlapEntry.offset + overlapEntry.mappingSize) - offset_next,
                    dirty       = overlapEntry.dirty
                )
                remainMappings.append(rightEntry)
                invalidOffsetNext = offset_next

            self.blockManager.invalidate_pages(overlapEntry.ppa + (invalidOffsetStart - overlapEntry.offset), invalidOffsetNext - invalidOffsetStart)
            #self.statistics.track_cmt_hit_by_page(invalidOffsetNext - invalidOffsetStart)
            remainingSize -= (invalidOffsetNext - invalidOffsetStart)
            current_offset = invalidOffsetNext

        self.statistics.track_cmt_hit_by_page(size - remainingSize)

        if remainingSize > 0:
            if not ftp:
                GTE = self.getGTE(TPIndex)
                if GTE and GTE.ppa != -1:
                   ftp = self.blockManager.read(GTE.ppa)
            if ftp:
                for offset in range(current_offset, offset_next):
                    ppa = ftp.getPPA(offset)
                    if ppa == -1:
                        continue
                    self.blockManager.invalidate_pages(ppa, 1)
                self.statistics.track_cmt_miss_by_page(1)
                self.statistics.track_cmt_hit_by_page(remainingSize - 1)
            else:
                self.statistics.track_cmt_hit_by_page(remainingSize)

        # remove old mappings
        for mapping in overlapEntries:
            CTP.removeCME_CCFTL(mapping)

        # insert remain left and right mappings
        for mapping in remainMappings:
            self.insertCME_CCFTL(TPIndex, mapping)

        # insert new mapping
        cme = CachedMappingEntry(
            offset      = offset_start,
            ppa         = newPPA,
            mappingSize = size,
            dirty       = True
        )
        self.insertCME_CCFTL(TPIndex, cme)

    def update_data_mapping_rFTL(self, lpa: int, newPPA: int, size: int):
        TPIndex = lpa // self.entries_per_ftp
        current_rGTD_Group = TPIndex // self.rGTD_GROUP
        prev_rGTD_Group    = (TPIndex - 1) // self.rGTD_GROUP

        remainMappings  = []

        # 1. lookup TPIndex in CMT
        CTP = self.getCTP(TPIndex, update_mru=True)

        # 2. CMT doesn't contain TPIndex
        if not CTP:
            GTE = self.getGTE(TPIndex)

            if not GTE:
                self.statistics.track_cmt_hit_by_page(size)    

                leftCTP = self.getCTP(TPIndex-1, update_mru=True)

                # 2.2.2. CMT doesn't contain TPIndex-1
                #if (not leftCTP) or (leftCTP.TPSize >= self.maxTPSize) or (len(leftCTP.mappingList) > (self.entries_per_ftp * leftCTP.TPSize / (leftCTP.TPSize + 1))):
                if (current_rGTD_Group != prev_rGTD_Group) or (not leftCTP) or (len(leftCTP.mappingList) > (self.entries_per_ftp * leftCTP.TPSize / (leftCTP.TPSize + 1))):
                    # if current_rGTD_Group != prev_rGTD_Group:
                    #     print(f"INFO: TPIndex {TPIndex} is in a new rGTD group ({current_rGTD_Group}) compared to TPIndex-1 ({prev_rGTD_Group}). Creating a new GTE for TPIndex {TPIndex}.")

                    GTE = GlobalTranslationEntry(
                        TPIndex = TPIndex,
                        valid = True
                    )
                    self.insertGTE(GTE)
                    CTP = CachedTranslationPage(TPIndex)
                    self.insertCTP_rFTL(CTP)

                # 2.2.3. CMT contains TPIndex-1
                else:
                    leftCTP.TPSize += 1
                    CTP             = leftCTP
                    GTE = self.getGTE(TPIndex-1)
                    if GTE is None:
                        raise Exception(f"ERROR!!! GTE for TPIndex-1 ({TPIndex-1}) not found in GTD!")
                    GTE.TPSize += 1
                    #self.GTD.insertGTE(GTE)

            else:
                self.statistics.track_cmt_miss_by_page(1)
                self.statistics.track_cmt_hit_by_page(size - 1)
                ftp = self.blockManager.read(GTE.ppa)
                if not ftp:
                    raise Exception("ERROR!!! GTD is faulty!")
                cmeList, offsetList = self.build_cmeList_from_fmeList(ftp.data)

                CTP = CachedTranslationPage(
                    TPIndex     = GTE.TPIndex,
                    mappingList = cmeList,
                    mappingOffsets = offsetList,
                    TPSize      = GTE.TPSize
                )
                self.insertCTP_rFTL(CTP)
        else:
            self.statistics.track_cmt_hit_by_page(size)
            
        # 3. CMT contains TPIndex
        offset_start = lpa %  self.entries_per_ftp + (TPIndex - CTP.TPIndex) * self.entries_per_ftp
        offset_end   = offset_start + size

        overlapEntries = CTP.getOverlapEntries_rFTL(offset_start, size)

        for mapping in overlapEntries:
            mapping_start = mapping.offset
            mapping_end   = mapping.offset + mapping.mappingSize

            overlap_start = max(offset_start, mapping_start)
            overlap_end   = min(offset_end,   mapping_end)
    
            # remain left mapping
            if mapping_start < overlap_start:
                left_mapping = CachedMappingEntry(
                    offset      = mapping_start,
                    ppa         = mapping.ppa,
                    mappingSize = overlap_start - mapping_start
                )
                remainMappings.append(left_mapping)

            # remain right mapping
            if mapping_end > overlap_end:
                right_mapping = CachedMappingEntry(
                    offset      = overlap_end,
                    ppa         = mapping.ppa + overlap_end - mapping_start,
                    mappingSize = mapping_end - overlap_end
                )
                remainMappings.append(right_mapping)

            self.blockManager.invalidate_pages(mapping.ppa + overlap_start - mapping_start, overlap_end - overlap_start)

        # remove old mappings
        for mapping in overlapEntries:
            CTP.removeCME_rFTL(mapping)

        # insert remain left and right mappings
        for mapping in remainMappings:
            self.insertCME_rFTL(TPIndex, mapping)

        # insert new mapping
        new_mapping = CachedMappingEntry(
            offset      = offset_start,
            ppa         = newPPA,
            mappingSize = size
        )
        self.insertCME_rFTL(TPIndex, new_mapping)
        CTP.dirty = True
        if len(CTP.mappingList) >= self.entries_per_ftp:
            mruCTP = self.popMruCTP()
            if mruCTP is not CTP:
                raise Exception("Something wrong ....")
            self.flushCTP(mruCTP)

    def insertCME_CDFTL(self, cme: CachedMappingEntry):
        if self.isCacheFull():
            victimCME = self.popVictimCME()
            if victimCME.isDirty():
                TPIndex = victimCME.offset // self.entries_per_ftp
                offset  = victimCME.offset % self.entries_per_ftp
                GTE = self.getGTE(TPIndex)
                CTP = self.getCTP(TPIndex)
                if CTP:
                    assert(GTE.isLocatedInCache())
                    CTP.updatePPA(offset, victimCME.ppa)
                else:
                    assert(GTE.isLocatedInFlash())
                    ftp = self.blockManager.read(GTE.ppa)
                    self.blockManager.invalidate_pages(GTE.ppa, 1)
                    ftp.updatePPA(offset, victimCME.ppa)
                    ppa, _ = self.blockManager.try_to_allocate_pages(1, BlockType.TRANSLATION)
                    self.blockManager.write(ppa, ftp, time.time_ns())
                    GTE.ppa = ppa
            
            redundantCMEs, num = self.getRedundantCMEs()
            if num >= self.entries_per_ftp:
                for TPIndex, lpaList in redundantCMEs.items():
                    CTP = self.getCTP(TPIndex)
                    GTE = self.getGTE(TPIndex)
                    for lpa in lpaList:
                        entry = self.popCME(lpa)
                        offset = lpa % self.entries_per_ftp
                        CTP.updatePPA(offset, entry.ppa)
                        GTE.dirty = True
            else:
                if len(self.CMT.cache) > 1:
                    victimCTP = self.popVictimCTP()
                    GTE = self.getGTE(victimCTP.TPIndex)
                    if GTE.isDirty():
                        ftp = FlashPage(
                            data           = victimCTP.mappingList,
                            lpa_or_TPIndex = victimCTP.TPIndex,
                            pageType       = PageType.TRANSLATION,
                            nEntry         = self.entries_per_ftp
                        )
                        ppa, _ = self.blockManager.try_to_allocate_pages(1, BlockType.TRANSLATION)
                        if ppa == -1:
                            print("WARNING! No more free pages!")
                            quit()
                        self.blockManager.write(ppa, ftp, time.time_ns())
                        if GTE.ppa != -1:
                            self.blockManager.invalidate_pages(GTE.ppa, 1)
                        GTE.ppa = ppa
                        GTE.dirty = False
                    GTE.moveToFlash()
        self.timingTracker.record_dram_access(is_write=True)
        self.CMT.insertCME_DFTL_CDFTL(cme)

    def getRedundantCMEs(self) -> tuple[dict, int]:
        self.timingTracker.record_dram_access(is_write=False)
        return self.CMT.getRedundantCMEs()

    def insertCME_DFTL(self, cme: CachedMappingEntry):
        if self.isCacheFull():
            victimCME = self.popVictimCME()
            if victimCME.isDirty():
                TPIndex = victimCME.offset // self.entries_per_ftp
                offset  = victimCME.offset % self.entries_per_ftp
                gte = self.getGTE(TPIndex)
                if gte.ppa != -1:
                    ftp = self.blockManager.read(gte.ppa)
                    self.blockManager.invalidate_pages(gte.ppa, 1)
                else:
                    ftp = FlashPage(
                        data           = [FlashMappingEntry(-1, False) for _ in range(self.entries_per_ftp)],
                        lpa_or_TPIndex = TPIndex,
                        pageType       = PageType.TRANSLATION,
                        nEntry         = self.entries_per_ftp
                    )
                ftp.updatePPA(offset, victimCME.ppa)
                ppa, _ = self.blockManager.try_to_allocate_pages(1, BlockType.TRANSLATION)
                if ppa == -1:
                    print("WARNING! No more free pages!")
                    quit()
                self.blockManager.write(ppa, ftp, time.time_ns())
                gte.ppa = ppa
        self.timingTracker.record_dram_access(is_write=True)
        self.CMT.insertCME_DFTL_CDFTL(cme)

    def insertCME_CCFTL(self, TPIndex: int, cme: CachedMappingEntry):
        if self.isCacheFull():
            victimCTP = self.getVictimCTP()
            victimCME = victimCTP.popVictimCME()
            if victimCME.isDirty():
                gte = self.getGTE(victimCTP.TPIndex)
                if gte.ppa != -1:
                    ftp = self.blockManager.read(gte.ppa)
                    self.blockManager.invalidate_pages(gte.ppa, 1)
                else:
                    ftp = FlashPage(
                        data           = [FlashMappingEntry(-1, False) for _ in range(self.entries_per_ftp)],
                        lpa_or_TPIndex = victimCTP.TPIndex,
                        pageType       = PageType.TRANSLATION,
                        nEntry         = self.entries_per_ftp
                    )

                for i in range(victimCME.mappingSize):
                    ftp.updatePPA(victimCME.offset + i, victimCME.ppa + i)

                ppa, _ = self.blockManager.try_to_allocate_pages(1, BlockType.TRANSLATION)
                if ppa == -1:
                    print("WARNING! No more free pages!")
                    quit()
                self.blockManager.write(ppa, ftp, time.time_ns())
                gte.ppa = ppa

            if len(victimCTP.mappingList) == 0:
                self.removeVictimCTP()

        self.timingTracker.record_dram_access(is_write=True)
        self.CMT.insertCME_CCFTL_rFTL(TPIndex, cme)

    def insertCME_rFTL(self, TPIndex: int, cme: CachedMappingEntry):
        self.timingTracker.record_dram_access(is_write=True)
        if self.isCacheFull():
            victimCTP = self.popVictimCTP()
            self.flushCTP(victimCTP)
        targetCTP = self.getCTP(TPIndex)
        targetCTP.insertCME_rFTL(cme)

    def insertCTP_CDFTL(self, CTP: CachedTranslationPage):
        if self.isCTPInsertionSafe(CTP) == False:
            victimCTP = self.popVictimCTP()
            GTE = self.getGTE(victimCTP.TPIndex)
            if GTE.isDirty():
                ftp = FlashPage(
                    data           = victimCTP.mappingList,
                    lpa_or_TPIndex = victimCTP.TPIndex,
                    pageType       = PageType.TRANSLATION,
                    nEntry         = self.entries_per_ftp
                )
                ppa, _ = self.blockManager.try_to_allocate_pages(1, BlockType.TRANSLATION)
                self.blockManager.write(ppa, ftp, time.time_ns())
                if GTE.ppa != -1:
                    self.blockManager.invalidate_pages(GTE.ppa, 1)
                GTE.ppa = ppa
                GTE.dirty = False
            GTE.moveToFlash()
        self.timingTracker.record_dram_access(is_write=True)
        self.CMT.insertCTP(CTP)
        
        # Sync with existing dirty CMEs
        GTE = self.getGTE(CTP.TPIndex)
        lpa_start = CTP.TPIndex * self.entries_per_ftp 
        for i in range(self.entries_per_ftp):
            lpa = lpa_start + i
            cme = self.CMT.dftl_cmt.get(lpa)
            if cme and cme.isDirty():
                CTP.updatePPA(i, cme.ppa)
                cme.dirty = False
                GTE.dirty = True

    def insertCTP_CCFTL(self, CTP: CachedTranslationPage):
        while self.CMT.isCTPInsertionSafe(CTP) == False:
            victimCTP = self.getVictimCTP()
            victimCME = victimCTP.popVictimCME()
            if victimCME.isDirty():
                ftp = None
                gte = self.getGTE(victimCTP.TPIndex)
                if gte.ppa != -1:
                    ftp = self.blockManager.read(gte.ppa)
                    self.blockManager.invalidate_pages(gte.ppa, 1)
                else:
                    ftp = FlashPage(
                        data           = [FlashMappingEntry(-1, False) for _ in range(self.entries_per_ftp)],
                        lpa_or_TPIndex = victimCTP.TPIndex,
                        pageType       = PageType.TRANSLATION,
                        nEntry         = self.entries_per_ftp
                    )

                for i in range(victimCME.mappingSize):
                    ftp.updatePPA(victimCME.offset + i, victimCME.ppa + i)

                ppa, _ = self.blockManager.try_to_allocate_pages(1, BlockType.TRANSLATION)
                if ppa == -1:
                    print("WARNING! No more free pages!")
                    quit()
                self.blockManager.write(ppa, ftp, time.time_ns())
                gte.ppa = ppa

            if len(victimCTP.mappingList) == 0:
                self.removeVictimCTP()

        self.timingTracker.record_dram_access(is_write=True)
        self.CMT.insertCTP(CTP)

    def insertCTP_rFTL(self, CTP: CachedTranslationPage):
        while self.CMT.isCTPInsertionSafe(CTP) == False:
            victimCTP = self.popVictimCTP()
            self.flushCTP(victimCTP)
        self.timingTracker.record_dram_access(is_write=True)
        self.CMT.insertCTP(CTP)

    def isCTPInsertionSafe(self, CTP: CachedTranslationPage) -> bool:
        return self.CMT.isCTPInsertionSafe(CTP)

    def flushCTP(self, victimCTP: CachedTranslationPage):
        if not victimCTP.dirty:
            return

        # remove old GTE
        self.removeGTE(victimCTP.TPIndex)
        
        # Pack TPs into FTPs efficiently
        tp_offset     = 0
        cmeStartIndex = 0
        while tp_offset < victimCTP.TPSize:
            if cmeStartIndex >= len(victimCTP.mappingList):
                break
            nextTPOffset = victimCTP.mappingList[cmeStartIndex].offset // self.entries_per_ftp
            if nextTPOffset > tp_offset:
                tp_offset = nextTPOffset
            # Try to pack as many remaining TPs as possible
            FTP, FTPSize, actualCMEs = self.try_to_build_FTP(victimCTP, tp_offset, cmeStartIndex)
            if FTPSize == 0:
                raise Exception("ERROR!!! Failed to build translation page!")
            
            ppa, allocatedSize = self.blockManager.try_to_allocate_pages(1, BlockType.TRANSLATION)
            if ppa == -1 or allocatedSize == 0:
                raise Exception("ERROR!!! Failed to allocate page for translation page!")
            self.blockManager.write(ppa, FTP, time.time_ns())
            
            # Update GTD
            GTE = GlobalTranslationEntry(
                TPIndex = FTP.lpa_or_TPIndex,
                ppa     = ppa,
                TPSize  = FTPSize,
                nEntry  = FTP.nEntry,
                valid   = True
            )
            self.insertGTE(GTE)
            tp_offset     += FTPSize
            cmeStartIndex += actualCMEs
        
        # Mark CTP as clean after all TPs are flushed
        victimCTP.dirty = False

    def flushCMT(self):
        if self.FTLScheme == "DFTL":
            self.flushCMT_DFTL()
        elif self.FTLScheme == "CDFTL":
            self.flushCMT_CDFTL()
        elif self.FTLScheme == "CCFTL":
            self.flushCMT_CCFTL()
        elif self.FTLScheme == "rFTL":
            self.flushCMT_rFTL()
        else:
            raise Exception("ERROR!!! Not support current FTL scheme!")

    def flushCMT_DFTL(self):
        dirty_entries = {}  # TPIndex -> list of CME
        for lpa, cme in self.CMT.dftl_cmt.items():
            if cme.isDirty():
                TPIndex = lpa // self.entries_per_ftp
                if TPIndex not in dirty_entries:
                    dirty_entries[TPIndex] = []
                dirty_entries[TPIndex].append(cme)

        for TPIndex, cmeList in dirty_entries.items():
            gte = self.getGTE(TPIndex)
            ftp = None
            if gte.ppa != -1:
                ftp = self.blockManager.read(gte.ppa)
                self.blockManager.invalidate_pages(gte.ppa, 1)
            else:
                ftp = FlashPage(
                    data           = [FlashMappingEntry(1, False) for _ in range(self.entries_per_ftp)],
                    lpa_or_TPIndex = TPIndex,
                    pageType       = PageType.TRANSLATION,
                    nEntry         = self.entries_per_ftp
                )
            
            for cme in cmeList:
                offset = cme.offset % self.entries_per_ftp
                ftp.updatePPA(offset, cme.ppa)
                cme.dirty = False

            ppa, _ = self.blockManager.try_to_allocate_pages(1, BlockType.TRANSLATION)
            self.blockManager.write(ppa, ftp, time.time_ns())
            gte.ppa = ppa

    def flushCMT_CDFTL(self):
        dirty_entries = {}  # TPIndex -> list of CME
        for lpa, cme in self.CMT.dftl_cmt.items():
            if cme.isDirty():
                TPIndex = lpa // self.entries_per_ftp
                if TPIndex not in dirty_entries:
                    dirty_entries[TPIndex] = []
                dirty_entries[TPIndex].append(cme)

        for TPIndex, cmeList in dirty_entries.items():
            GTE = self.getGTE(TPIndex)
            CTP = self.getCTP(TPIndex)
            if CTP:
                for cme in cmeList:
                    CTP.updatePPA(cme.offset % self.entries_per_ftp, cme.ppa)
                GTE.dirty = True
            else:
                ftp = self.blockManager.read(GTE.ppa)
                for cme in cmeList:
                    ftp.updatePPA(cme.offset % self.entries_per_ftp, cme.ppa)
                ppa, _ = self.blockManager.try_to_allocate_pages(1, BlockType.TRANSLATION)
                self.blockManager.write(ppa, ftp, time.time_ns())
                if GTE.ppa != -1:
                    self.blockManager.invalidate_pages(GTE.ppa, 1)
                GTE.ppa = ppa

        for CTP in self.CMT.cache:
            GTE = self.getGTE(CTP.TPIndex)
            if GTE.isDirty():
                ftp = FlashPage(
                    data           = CTP.mappingList,
                    lpa_or_TPIndex = CTP.TPIndex,
                    pageType       = PageType.TRANSLATION,
                    nEntry         = self.entries_per_ftp
                )
                ppa, _ = self.blockManager.try_to_allocate_pages(1, BlockType.TRANSLATION)
                self.blockManager.write(ppa, ftp, time.time_ns())
                if GTE.ppa != -1:
                    self.blockManager.invalidate_pages(GTE.ppa, 1)
                GTE.dirty = False
                GTE.ppa = ppa
            GTE.moveToFlash()

    def flushCMT_CCFTL(self):
        dirty_entries = {}  # TPIndex -> list of CME
        for ctp in self.CMT.cache:
            TPIndex = ctp.TPIndex
            for cme in ctp.mappingList:
                if cme.isDirty():
                    if TPIndex not in dirty_entries:
                        dirty_entries[TPIndex] = []
                    dirty_entries[TPIndex].append(cme)

        for TPIndex, cmes in dirty_entries.items():
            gte = self.getGTE(TPIndex)
            ftp = None
            if gte.ppa != -1:
                ftp = self.blockManager.read(gte.ppa)
                self.blockManager.invalidate_pages(gte.ppa, 1)
            else:
                ftp = FlashPage(
                    data           = [FlashMappingEntry(1, False) for _ in range(self.entries_per_ftp)],
                    lpa_or_TPIndex = TPIndex,
                    pageType       = PageType.TRANSLATION,
                    nEntry         = self.entries_per_ftp
                )
            
            for cme in cmes:
                for i in range(cme.mappingSize):
                    ftp.updatePPA(cme.offset + i, cme.ppa + i)
                cme.dirty = False
            
            ppa, _ = self.blockManager.try_to_allocate_pages(1, BlockType.TRANSLATION)
            self.blockManager.write(ppa, ftp, time.time_ns())
            gte.ppa = ppa

    def flushCMT_rFTL(self):
        for CTP in self.CMT.cache:
            if not CTP.dirty:
                continue

            # remove old GTE
            self.removeGTE(CTP.TPIndex)
            
            # Pack TPs into FTPs efficiently
            tp_offset     = 0
            cmeStartIndex = 0
            while tp_offset < CTP.TPSize:
                if cmeStartIndex >= len(CTP.mappingList):
                    break
                nextTPOffset = CTP.mappingList[cmeStartIndex].offset // self.entries_per_ftp
                if nextTPOffset > tp_offset:
                    tp_offset = nextTPOffset
                # Try to pack as many remaining TPs as possible
                FTP, FTPSize, actualCMEs = self.try_to_build_FTP(CTP, tp_offset, cmeStartIndex)
                if FTPSize == 0:
                    raise Exception("ERROR!!! Failed to build translation page!")
                
                ppa, allocatedSize = self.blockManager.try_to_allocate_pages(1, BlockType.TRANSLATION)
                if ppa == -1 or allocatedSize == 0:
                    raise Exception("ERROR!!! Failed to allocate page for translation page!")
                self.blockManager.write(ppa, FTP, time.time_ns())
                
                # Update GTD
                GTE = GlobalTranslationEntry(
                    TPIndex = FTP.lpa_or_TPIndex,
                    ppa     = ppa,
                    TPSize  = FTPSize,
                    nEntry  = FTP.nEntry,
                    valid   = True
                )
                self.insertGTE(GTE)
                tp_offset     += FTPSize
                cmeStartIndex += actualCMEs
            
            # Mark CTP as clean after all TPs are flushed
            CTP.dirty = False

    def build_ppa_ranges(self, ppaList: list[int]) -> list[tuple[int, int]]:
        ppa_ranges: list[tuple[int, int]] = []
        if len(ppaList) > 0:
            start_ppa = ppaList[0]
            range_size = 1
            for ppa in ppaList[1:]:
                if ppa == start_ppa + range_size:
                    range_size += 1
                else:
                    ppa_ranges.append((start_ppa, range_size))
                    start_ppa = ppa
                    range_size = 1
            ppa_ranges.append((start_ppa, range_size))
        return ppa_ranges

    def try_to_build_FTP(self, CTP: CachedTranslationPage, tp_offset: int, cmeStartIndex: int) -> tuple[FlashPage, int, int]:
        fmeList : list[FlashMappingEntry] = []
        FTPSize = 0
        cmeIndex = cmeStartIndex
        encodedOffset = tp_offset * self.entries_per_ftp

        for currentTPOffset in range(tp_offset, CTP.TPSize):
            currentOffset     = encodedOffset
            currentOffsetNext = (currentTPOffset + 1) * self.entries_per_ftp
            tempFMEList : list[FlashMappingEntry] = []
            tempCMEIndex = cmeIndex

            while tempCMEIndex < len(CTP.mappingList):
                cme = CTP.mappingList[tempCMEIndex]
                if cme.offset >= currentOffsetNext:
                    break

                if currentOffset < cme.offset:
                    dummyEntry = FlashMappingEntry(
                        ppa         = cme.offset - currentOffset,
                        valid       = False,
                        mappingSize = 0
                    )
                    tempFMEList.append(dummyEntry)
                    currentOffset = cme.offset

                fme = FlashMappingEntry(
                    ppa         = cme.ppa,
                    valid       = True,
                    mappingSize = cme.mappingSize
                )
                tempFMEList.append(fme)
                currentOffset += cme.mappingSize
                tempCMEIndex += 1

            if len(tempFMEList) == 0:
                break

            if len(fmeList) + len(tempFMEList) > self.entries_per_ftp:
                break

            fmeList.extend(tempFMEList)
            FTPSize += 1
            cmeIndex = tempCMEIndex
            encodedOffset = currentOffset

        ftp = FlashPage(
            data           = fmeList,
            lpa_or_TPIndex = CTP.TPIndex + tp_offset,
            pageType       = PageType.TRANSLATION,
            nEntry         = len(fmeList)
        )

        return (ftp, FTPSize, cmeIndex - cmeStartIndex)

    def update_translation_mapping(self, TPIndex: int, ppa: int):
        GTE = self.getGTE(TPIndex)
        if not GTE:
            raise Exception("ERROR!!! GTD is corrupt!")
        oldPPA  = GTE.ppa
        GTE.ppa = ppa
        if oldPPA > -1:
            self.blockManager.invalidate_pages(oldPPA, 1)

    def isCacheFull(self) -> bool:
        return self.CMT.isFull()

    def getCTP(self, TPIndex: int, update_mru: bool = False) -> CachedTranslationPage:
        self.timingTracker.record_dram_access(is_write=False)
        ctp = self.CMT.getCTP(TPIndex, update_mru)
        if ctp and update_mru:
            self.timingTracker.record_dram_access(is_write=True)
        return ctp

    def getVictimCTP(self) -> CachedTranslationPage:
        self.timingTracker.record_dram_access(is_write=False)
        return self.CMT.getVictimCTP()

    def getGTE(self, TPIndex: int) -> GlobalTranslationEntry:
        # GTD access
        self.timingTracker.record_dram_access(is_write=False)
        return self.GTD.getGTE(TPIndex)

    def insertGTE(self, GTE: GlobalTranslationEntry):
        self.timingTracker.record_dram_access(is_write=True)
        self.GTD.insertGTE(GTE)

    def removeGTE(self, TPIndex: int):
        self.timingTracker.record_dram_access(is_write=True)
        gte = self.GTD.getGTE(TPIndex)
        if gte is None:
            raise Excetion("ERROR!!! Remove an invalid GTE!")
        if gte.ppa != -1:
            self.blockManager.invalidate_pages(gte.ppa, 1)
        self.GTD.removeGTE(TPIndex)

    def popVictimCME(self) -> CachedMappingEntry:
        self.timingTracker.record_dram_access(is_write=True) # pop latency is write because updating pointer
        return self.CMT.popVictimCME()
    
    def popCME(self, lpa: int) -> CachedMappingEntry:
        self.timingTracker.record_dram_access(is_write=True)
        return self.CMT.popCME(lpa)

    def getCME(self, lpa: int) -> CachedMappingEntry:
        self.timingTracker.record_dram_access(is_write=False)
        cme = self.CMT.getCME(lpa)
        if cme and (self.FTLScheme == "DFTL" or self.FTLScheme == "CDFTL"):
            # For DFTL/CDFTL, reading CME updates its LRU timestamp (a write operation)
            self.timingTracker.record_dram_access(is_write=True)
        return cme

    def removeVictimCTP(self):
        self.timingTracker.record_dram_access(is_write=True)
        victimCTP = self.CMT.popVictimCTP()
        if len(victimCTP.mappingList) > 0:
            raise Exception("ERROR!!! Remove a non-empty CTP!")

    def popVictimCTP(self) -> CachedTranslationPage:
        self.timingTracker.record_dram_access(is_write=True)
        return self.CMT.popVictimCTP()

    def popMruCTP(self) -> CachedTranslationPage:
        self.timingTracker.record_dram_access(is_write=True)
        return self.CMT.popMruCTP()

    def dumpGTD(self):
        self.GTD.dump_entries()

    def get_translation_page_usage_statistics(self):
        valid_entries_per_tp = {}
        for gte in self.GTD.iter_entries():
            # if gte.ppa == -1:
            #     raise Exception("ERROR!!! GTD is corrupt!")
            if gte.valid == False:
                continue
            valid_offsets = set()
            mappings = 0
            ftp = self.blockManager.peek(gte.ppa)
            for offset, entry in enumerate(ftp.data):
                if entry.isValid():
                    valid_offsets.add(offset)
                    mappings += entry.mappingSize
            valid_entries_per_tp[gte.TPIndex] = (len(valid_offsets), mappings)
        self.statistics.track_translation_page_usage(valid_entries_per_tp)

    def build_cmeList_from_fmeList(self, fmeList: list[FlashMappingEntry]) -> list[CachedMappingEntry]:
        cmeList: list[CachedMappingEntry] = []
        offsetList: list[int] = []
        offset = 0
        for flashEntry in fmeList:
            mappingSize  = flashEntry.mappingSize
            if flashEntry.valid:  # Chỉ load các entry hợp lệ
                cme = CachedMappingEntry(
                    offset      = offset,
                    ppa         = flashEntry.ppa,
                    mappingSize = mappingSize
                )
                cmeList.append(cme)
                offsetList.append(offset)
                offset += mappingSize
            else:
                offset += flashEntry.ppa    # dummy size

        return cmeList, offsetList

    def print_status(self, detail: bool = False):
        print("")
        print("======================================")
        print("=== Mapping Manager Status ===")
        print("======================================")
        print("")
        self.CMT.print_status(detail)
        self.GTD.print_status()
