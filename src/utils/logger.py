import logging
import sys

def get_logger(name: str) -> logging.Logger:
    """
    공통 로거 인스턴스를 반환합니다.
    stdout으로 스트림을 내보내며 시간, 로거명, 레벨, 메시지 포맷을 유지합니다.
    """
    logger = logging.getLogger(name)
    
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(formatter)
        logger.addHandler(ch)
        
    return logger
