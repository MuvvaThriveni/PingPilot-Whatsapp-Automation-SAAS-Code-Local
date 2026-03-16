"""Logs routes – message log retrieval and export (Phase-6: hardened).

Security fix: CSV formula injection protection in export.
"""

import io
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from db_layer.messages import messages as _db_messages

router = APIRouter(prefix="/api/logs", tags=["logs"])


def _sanitize_csv_value(value: str) -> str:
    """Prevent CSV formula injection (DDE attacks).

    If a cell value starts with =, +, -, @, or tab/CR/LF, prefix it with
    a single quote to prevent Excel/LibreOffice from interpreting it as a formula.
    """
    if not value:
        return value
    if value[0] in ('=', '+', '-', '@', '\t', '\r', '\n'):
        return f"'{value}"
    return value


def _remap_db_log(doc: dict) -> dict:
    """Remap db_layer field names to legacy API format."""
    return {
        "product_type": doc.get("product_type", ""),
        "recipient": doc.get("contact_phone", doc.get("recipient", "")),
        "message_id": doc.get("wa_message_id", doc.get("message_id", "")),
        "template_name": doc.get("template_name", ""),
        "status": doc.get("status", ""),
        "error_message": doc.get("error_message", ""),
        "campaign_id": doc.get("campaign_id", ""),
        "created_at": doc.get("created_at", ""),
    }


@router.get("")
async def get_logs(
    request: Request,
    product_type: str = None,
    status: str = None,
    limit: int = 25,
    cursor: str = None,
):
    tenant_id = request.state.tenant_id
    db_docs, next_cursor = await _db_messages.list(
        tenant_id,
        product_type=product_type,
        status=status,
        limit=limit,
        cursor=cursor,
    )
    return {
        "logs": [_remap_db_log(d) for d in db_docs],
        "total": len(db_docs),
        "limit": limit,
        "next_cursor": next_cursor,
    }


@router.get("/export")
async def export_logs(request: Request, product_type: str = None, status: str = None):
    tenant_id = request.state.tenant_id
    # Paginate through up to 5000 records for export
    all_docs: list[dict] = []
    cur = None
    while len(all_docs) < 5000:
        batch, cur = await _db_messages.list(
            tenant_id,
            product_type=product_type,
            status=status,
            limit=100,
            cursor=cur,
        )
        all_docs.extend(batch)
        if not cur:
            break
    filtered = [_remap_db_log(d) for d in all_docs]

    headers = [
        "Product Type", "Recipient", "Message ID", "Template",
        "Status", "Error", "Campaign ID", "Created At",
    ]
    rows = [",".join(headers)]
    for log in filtered:
        row = [
            _sanitize_csv_value(str(log.get("product_type", ""))),
            _sanitize_csv_value(str(log.get("recipient", ""))),
            _sanitize_csv_value(str(log.get("message_id", ""))),
            _sanitize_csv_value(str(log.get("template_name", ""))),
            _sanitize_csv_value(str(log.get("status", ""))),
            _sanitize_csv_value(str(log.get("error_message", ""))),
            _sanitize_csv_value(str(log.get("campaign_id", ""))),
            _sanitize_csv_value(str(log.get("created_at", ""))),
        ]
        rows.append(",".join([f'"{v}"' for v in row]))
    csv_content = "\n".join(rows)

    return StreamingResponse(
        io.StringIO(csv_content),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=message_logs.csv"},
    )


@router.get("/stats")
async def get_log_stats(request: Request):
    tenant_id = request.state.tenant_id
    db_stats = await _db_messages.get_stats(tenant_id)
    return {"stats": db_stats, "dailyStats": []}
