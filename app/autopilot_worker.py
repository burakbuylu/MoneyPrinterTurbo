"""
Autopilot worker giris noktasi.

Docker servisi olarak surekli calisir: `python3 -m app.autopilot_worker`
Belirli araliklarla titles.txt'den siradaki basligi alir, video uretir ve
(yapilandirilmissa) YouTube'a yukler.
"""

from loguru import logger

from app.services import autopilot


def main():
    try:
        autopilot.run_forever()
    except KeyboardInterrupt:
        logger.info("Autopilot worker interrupted; exiting.")


if __name__ == "__main__":
    main()
