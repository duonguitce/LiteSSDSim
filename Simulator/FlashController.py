import os
from Statistics import Statistics
from TimingTracker import TimingTracker
from Utilities import Utilities

class FlashController:
    def __init__(self, config, stattistics: Statistics, timingTracker: TimingTracker):
        self.pages_per_block   = config.pages_per_block
        self.page_size         = config.page_size
        self.flash_file        = config.flash_data

        self.statistics        = stattistics
        self.timingTracker     = timingTracker 

        #FlashArray
        if os.path.exists(self.flash_file):
            self.FlashArray = Utilities.load_from_file_with_pickle(self.flash_file)
        else:
            self.FlashArray = {}

    def dump_flash_array(self):
        Utilities.dump_to_file_with_pickle(self.FlashArray, self.flash_file)

    def read(self, ppa: int) -> any:
        if ppa not in self.FlashArray:
            print(f"ERROR!!! Flash: read a ghost page at PPA {ppa}")
            raise Exception("ERROR!!! Flash: read a ghost page")
        self.statistics.track_flash_read(ppa)
        self.timingTracker.record_read(ppa, self.page_size)
        return self.FlashArray[ppa]

    def peek(self, ppa: int) -> any:
        if ppa not in self.FlashArray:
            raise Exception("ERROR!!! Flash: read a ghost page")
        return self.FlashArray[ppa]

    def write(self, ppa: int, page: any):
        if ppa in self.FlashArray:
            raise Exception(f"ERROR!!! Flash: in-place update at PPA {ppa}")
        self.statistics.track_flash_write(ppa)
        self.timingTracker.record_write(ppa, self.page_size)
        self.FlashArray[ppa] = page
        
    def erase(self, blockID):
        self.statistics.track_flash_erase(blockID)
        self.timingTracker.record_erase(blockID)
        startPPA          = blockID * self.pages_per_block
        nextBlockStartPPA = (blockID + 1) * self.pages_per_block
        for ppa in range(startPPA, nextBlockStartPPA):
            if ppa in self.FlashArray:
                del self.FlashArray[ppa]