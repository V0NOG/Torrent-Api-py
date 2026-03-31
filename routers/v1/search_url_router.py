from fastapi import APIRouter, status
from helper.is_site_available import check_if_site_available
from helper.error_messages import error_handler

router = APIRouter(tags=["Torrent By Url"])


@router.get("/")
@router.get("")
async def get_torrent_from_url(site: str, url: str):
    site = site.lower()
    all_sites = check_if_site_available(site)
    if all_sites:
        cls = all_sites[site].get("website")
        if not cls:
            return error_handler(
                status_code=status.HTTP_404_NOT_FOUND,
                json_message={"error": "Selected Site Not Available"},
            )

        if not hasattr(cls, "get_torrent_by_url"):
            return error_handler(
                status_code=status.HTTP_400_BAD_REQUEST,
                json_message={"error": f"Site '{site}' does not support torrent-by-url."},
            )

        resp = await cls().get_torrent_by_url(url)

        # NEW: scraper debug payload
        if isinstance(resp, dict) and resp.get("blocked"):
            return error_handler(
                status_code=status.HTTP_403_FORBIDDEN,
                json_message={"error": "Website Blocked / Scraper Failed", "debug": resp.get("debug"), "url": resp.get("url")},
            )

        if resp is None:
            return error_handler(
                status_code=status.HTTP_403_FORBIDDEN,
                json_message={"error": "Website Blocked Change IP or Website Domain."},
            )
        elif len(resp.get("data") or []) > 0:
            return resp
        else:
            return error_handler(
                status_code=status.HTTP_404_NOT_FOUND,
                json_message={"error": "Result not found."},
            )

    return error_handler(
        status_code=status.HTTP_404_NOT_FOUND,
        json_message={"error": "Selected Site Not Available"},
    )
