"""Logs routes – message log retrieval and export."""

import io
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from store import message_logs

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("")
async def get_logs(
    product_type: str = None,
    status: str = None,
    limit: int = 100,
    offset: int = 0,
):
    filtered = message_logs
    if product_type:
        filtered = [l for l in filtered if l.get("product_type") == product_type]
    if status:
        filtered = [l for l in filtered if l.get("status") == status]
    sorted_logs = sorted(filtered, key=lambda x: x.get("created_at", ""), reverse=True)
    paginated = sorted_logs[offset : offset + limit]
    return {"logs": paginated, "total": len(filtered), "limit": limit, "offset": offset}


@router.get("/export")
async def export_logs(product_type: str = None, status: str = None):
    filtered = message_logs
    if product_type:
        filtered = [l for l in filtered if l.get("product_type") == product_type]
    if status:
        filtered = [l for l in filtered if l.get("status") == status]

    headers = [
        "Product Type", "Recipient", "Message ID", "Template",
        "Status", "Error", "Campaign ID", "Created At",
    ]
    rows = [",".join(headers)]
    for log in filtered:
        row = [
            log.get("product_type", ""),
            log.get("recipient", ""),
            log.get("message_id", ""),
            log.get("template_name", ""),
            log.get("status", ""),
            log.get("error_message", ""),
            log.get("campaign_id", ""),
            log.get("created_at", ""),
        ]
        rows.append(",".join([f'"{str(v)}"' for v in row]))
    csv_content = "\n".join(rows)

    return StreamingResponse(
        io.StringIO(csv_content),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=message_logs.csv"},
    )


@router.get("/stats")
async def get_log_stats():
    stats = {}
    for log in message_logs:
        key = f"{log.get('product_type', 'unknown')}_{log.get('status', 'unknown')}"
        stats[key] = stats.get(key, 0) + 1
    return {"stats": stats, "dailyStats": []}
