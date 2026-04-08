from datetime import datetime, date, timezone, timedelta

def get_current_kst() -> datetime:
    """한국 표준시 기준 현재 시간을 반환합니다."""
    kst = timezone(timedelta(hours=9))
    return datetime.now(kst)

def parse_date_string(date_str: str, format_str: str = "%Y%m%d") -> date:
    """문자열 날짜를 date 객체로 파싱합니다."""
    return datetime.strptime(date_str, format_str).date()

def generate_available_at_for_eod(base_date: date) -> datetime:
    """
    EOD (End Of Day) 데이터에 대한 available_at 을 산출합니다.
    통상적으로 한국 시장은 15:30에 정규장이 마감되므로 보수적으로 16:00 (KST) 로 간주합니다.
    """
    kst = timezone(timedelta(hours=9))
    return datetime(base_date.year, base_date.month, base_date.day, 16, 0, 0, tzinfo=kst)
