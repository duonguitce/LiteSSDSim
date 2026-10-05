from SSDConfig import SSDConfig
from Utilities import Utilities
from enum import Enum, auto
import os

class TPLocation(Enum):
    CACHE = auto()
    FLASH = auto()

class GlobalTranslationEntry:
    def __init__(self, TPIndex: int, ppa: int = -1, TPSize: int = 1, nEntry: int = 0, valid: bool = False):
        self.TPIndex = TPIndex
        self.ppa     = ppa
        self.TPSize  = TPSize
        self.nEntry  = nEntry
        self.valid   = valid
        self.location = TPLocation.CACHE    #CDFTL
        self.dirty    = True    #CDFTL

    # @property
    # def TPSize(self) -> int:
    #     return self._size

    # @TPSize.setter
    # def TPSize(self, value: int):
    #     self._size = value

    # @property
    # def size(self) -> int:
    #     return self._size

    # @size.setter
    # def size(self, value: int):
    #     self._size = value

    def isLocatedInCache(self) -> bool:
        if self.location == TPLocation.CACHE:
            return True
        return False

    def isLocatedInFlash(self) -> bool:
        if self.location == TPLocation.FLASH:
            return True
        return False
    
    def moveToCache(self):
        self.location = TPLocation.CACHE

    def moveToFlash(self):
        self.location = TPLocation.FLASH

    def isDirty(self) -> bool:
        return self.dirty

    def display(self):
        print(f"(TPIndex, PPA, TPSize, nEntry, valid) = ({self.TPIndex}, {self.ppa}, {self.TPSize}, {self.nEntry}, {self.valid})")


class GlobalTranslationDirectory:
    def __init__(self, cfg: SSDConfig):
        self.gtd_translation_entry_size = cfg.gtd_translation_entry_size
        self.gtd_file                   = cfg.gtd_data
        self.rGTD_GROUP                 = cfg.rGTD_GROUP
        if self.rGTD_GROUP <= 0:
            raise ValueError("rGTD_GROUP must be greater than zero")

        if os.path.exists(self.gtd_file):
            self.entries = Utilities.load_from_file_with_pickle(self.gtd_file)
            # if isinstance(self.entries, list):
            #     old_entries = self.entries
            #     self.entries = {}
            #     for entry in old_entries:
            #         self.insertGTE(entry)
            # elif not isinstance(self.entries, dict):
            #     raise ValueError("Invalid GTD data: expected a dictionary")
        else:
            self.entries: dict[int, list[GlobalTranslationEntry | None]] = {}

    def dump_entries(self):
        Utilities.dump_to_file_with_pickle(self.entries, self.gtd_file)

    def getGTE(self, TPIndex: int) -> GlobalTranslationEntry:
        group, offset = divmod(TPIndex, self.rGTD_GROUP)
        entries = self.entries.get(group)
        if entries is None:
            return None

        entryOffset = 0
        while entryOffset < len(entries):
            entry = entries[entryOffset]
            if entry.valid == True:
                if (entryOffset <= offset) and (offset < entryOffset + entry.TPSize):
                    return entry
                entryOffset += entry.TPSize
            else:
                entryOffset += 1

        return None

    def insertGTE(self, GTE: GlobalTranslationEntry):
        group, offset = divmod(GTE.TPIndex, self.rGTD_GROUP)
        entries = self.entries.setdefault(group, [GlobalTranslationEntry(-1, -1, 1, 0, False) for _ in range(self.rGTD_GROUP)])

        # Check for overlapping entries
        entryOffset = 0
        while entryOffset < offset:
            entry = entries[entryOffset]
            if entry.valid == True:
                if (entryOffset <= offset) and (offset < entryOffset + entry.TPSize):
                    raise ValueError(f"Cannot insert GTE at TPIndex {GTE.TPIndex}: overlapping with existing GTE at TPIndex {entry.TPIndex}")
                entryOffset += entry.TPSize
            else:
                entryOffset += 1

        entries[offset] = GTE


    def removeGTE(self, TPIndex: int):
        group, offset = divmod(TPIndex, self.rGTD_GROUP)
        entries = self.entries.get(group)
        if entries is None:
            raise ValueError(f"No entries found for group {group}, TPIndex {TPIndex}, offset {offset}")

        entryOffset = 0
        while entryOffset < len(entries):
            entry = entries[entryOffset]
            if entry.valid == True:
                if (entryOffset <= offset) and (offset < entryOffset + entry.TPSize):
                    entry.valid = False
                    return
                entryOffset += entry.TPSize
            else:
                entryOffset += 1

        raise ValueError(f"No entries found for group {group}, TPIndex {TPIndex}, offset {offset}")

    def iter_entries(self):
        #seen = set()
        for entries in self.entries.values():
            for entry in entries:
                # if entry is not None and id(entry) not in seen:
                #     seen.add(id(entry))
                #     yield entry
                # if id(entry) in seen:
                #     raise ValueError(f"Duplicate entry found in GTD: {entry}")
                if entry.valid == True:
                    yield entry
                

    def print_status(self):
        print("************ GTD status **************")
        for entries in self.entries.values():
            for entry in entries:
                if entry.valid == True:
                    entry.display()
        print("\n**************************************")

