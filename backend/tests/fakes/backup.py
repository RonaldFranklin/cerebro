from cerebro.application.dto import LastBackup


class FakeBackupStatus:
    def __init__(self) -> None:
        self.record = LastBackup(state="never")

    def last(self) -> LastBackup:
        return self.record
