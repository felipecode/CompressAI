from compressai.utils.writer.writer import Writer


class DummyWriter(Writer):
    def write(self):
        return

    def write_metric(self, name, value, iter):
        return

    def watch_all(self, net):
        pass

    def write_image(self, name, value, iter):
        return

    def write_parameters(self, params):
        return

    def close(self):
        print("Close dummy writer")
        return