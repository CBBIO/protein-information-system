import logging
import os


def setup_logger(name, log_dir):
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    logger.propagate = False

    if log_dir and not os.path.exists(log_dir):
        log_dir = os.path.expanduser(log_dir)
        os.makedirs(log_dir, exist_ok=True)

    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    if log_dir:
        info_path = os.path.join(log_dir, 'info.log')
        info_handler = logging.FileHandler(info_path)
        info_handler.setLevel(logging.INFO)
        info_handler.setFormatter(formatter)
        logger.addHandler(info_handler)

        debug_path = os.path.join(log_dir, 'debug.log')
        debug_handler = logging.FileHandler(debug_path)
        debug_handler.setLevel(logging.DEBUG)
        debug_handler.setFormatter(formatter)
        logger.addHandler(debug_handler)

        logger.info(f"Logs are being save to: {log_dir}")

    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger
