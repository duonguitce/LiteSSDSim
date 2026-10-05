from BlockManager import BlockManager
from FlashLatencyModel import FlashLatencyModel
from DRAMLatencyModel import DRAMLatencyModel
from MappingManager import MappingManager
from GarbageCollector_and_WearLeveler import GarbageCollector_and_WearLeveler
from OperationEngine import OperationEngine
from SSDConfig import SSDConfig
from Statistics import Statistics
from TimingTracker import TimingTracker


class FlashTranslationLayer:    
    def __init__(self, ftlScheme, cfg: SSDConfig):
        self.statistics        = Statistics()
        self.flashLatencyModel = FlashLatencyModel(cfg)
        self.dramLatencyModel  = DRAMLatencyModel(cfg)
        self.timingTracker     = TimingTracker(cfg, self.flashLatencyModel, self.dramLatencyModel)
        self.blockManager      = BlockManager(cfg, self.statistics, self.timingTracker)
        self.mappingManager    = MappingManager(ftlScheme, cfg, self.blockManager, self.statistics, self.timingTracker)
        self.operationEngine   = OperationEngine(cfg, self.blockManager, self.mappingManager, self.statistics)
        self.GCnWL             = GarbageCollector_and_WearLeveler(cfg, self.blockManager, self.operationEngine, self.statistics)
        self.ftlScheme = ftlScheme

    def flush_cmt(self):
        self.mappingManager.flushCMT()

    def dump_gtd(self):
        self.mappingManager.dumpGTD()

    def dump_block_manager(self):
        self.blockManager.dump_block_manager_data()

    def dump_flash_array(self):
        self.blockManager.dump_flash_array()

    def writes(self, lpa: int, size: int, dataList: list[any], timestamp: int):
        self.statistics.track_host_write(lpa, size)
        isOK = self.operationEngine.writes_data_using_lpa(lpa, size, dataList, timestamp)
        if isOK == False:
            return False

        self.GCnWL.run()
        return True

    def reads(self, lpa: int, size: int) -> list[any]:
        self.statistics.track_host_read(lpa, size)
        return self.operationEngine.reads_data_using_lpa(lpa, size)

    def print_status(self):
        print("========================================")
        print(f"=== Flash Translation Layer Status: {self.ftlScheme} ===")
        print("========================================")
        self.mappingManager.get_translation_page_usage_statistics() # must call before print statistics to ensure stats are updated
        self.statistics.print_statistics()
        self.timingTracker.print_statistics()
