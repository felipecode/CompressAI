from abc import ABC, abstractmethod


class Writer(ABC):
    def __init__(self, offline=False):
        self.offline = offline

    def write(self):
        pass

    @abstractmethod
    def watch_all(self, net):
        pass

    @abstractmethod
    def write_metric(self, name, value, iter):
        pass

    def write_metrics(self, metrics, iter):
        for m, v in metrics.items():
            self.write_metric(m, v, iter)

    @abstractmethod
    def write_image(self, name, value, iter):
        pass

    @abstractmethod
    def write_parameters(self, params):
        pass

    @abstractmethod
    def close(self):
        pass


if __name__ == "__main__":
    w = Writer()
