"""Deterministic fabricated transaction examples; never a live fallback."""
from datetime import datetime, timezone


def sample_transactions(*, kind: str, lawd_cd: str, deal_ymd: str,
                        page_no: int, num_of_rows: int) -> dict:
    records = []
    for i in range(3):
        monthly = kind == "rent" and i == 2
        records.append({
            "id": f"SAMPLE-{kind}-{deal_ymd}-{i + 1}",
            "apartment_name": ["가상 한빛아파트", "가상 초록아파트", "가상 은솔아파트"][i],
            "legal_dong": "시연용 가상동",
            "contract_date": f"{deal_ymd[:4]}-{deal_ymd[4:]}-{10+i:02d}",
            "area_m2": [59.95, 84.97, 74.5][i], "floor": [3, 8, 12][i],
            "build_year": [2005, 2010, 2018][i],
            "price_krw": [1400000000, 1800000000, 1600000000][i] if kind == "trade" else None,
            "deposit_krw": [700000000, 950000000, 300000000][i] if kind == "rent" else None,
            "monthly_rent_krw": (1500000 if monthly else 0) if kind == "rent" else None,
            "cancelled": kind == "trade" and i == 2,
            "cancelled_date": f"{deal_ymd[:4]}-{deal_ymd[4:]}-20" if kind == "trade" and i == 2 else None,
            "lease_type": "trade" if kind == "trade" else ("monthly_rent" if monthly else "jeonse"),
        })
    start = (page_no - 1) * num_of_rows
    selected = records[start:start + num_of_rows]
    return {
        "source": "synthetic_sample", "synthetic": True, "kind": kind,
        "lawd_cd": lawd_cd, "deal_ymd": deal_ymd, "page_no": page_no,
        "num_of_rows": num_of_rows, "total_count": len(records),
        "returned_count": len(selected), "has_next": start + num_of_rows < len(records),
        "retrieved_at": datetime.now(timezone.utc).isoformat(), "items": selected,
        "warnings": [
            "화면 확인을 위한 가상 거래 자료입니다. 실제 국토부 조회 결과가 아닙니다.",
            "실거래 기록은 신고된 과거 계약이며 현재 매물·입주 가능 여부를 뜻하지 않습니다.",
        ],
    }
