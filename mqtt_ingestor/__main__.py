import logging
import signal

from mqtt_ingestor.config import Settings
from mqtt_ingestor.service import MQTTIngestor


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    ingestor = MQTTIngestor(Settings.from_env())

    def stop(signum, frame) -> None:
        ingestor.shutdown()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    ingestor.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
