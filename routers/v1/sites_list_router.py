# /home/von/Torrent-Api-py/routers/v1/sites_list_router.py

from fastapi import APIRouter, status
from helper.is_site_available import check_if_site_available, sites_config
from helper.error_messages import error_handler

router = APIRouter(tags=["Get all sites"])

# Always expose these sites in the UI even if availability checks fail
# (e.g. ABB might require login / be blocked by Cloudflare)
_ALWAYS_EXPOSE_SITES = {"audiobookbay"}


@router.get("/")
@router.get("")
async def get_all_supported_sites():
    all_sites = check_if_site_available("all")

    # Existing behavior: only include sites with a reachable "website"
    sites_list = [site for site in all_sites.keys() if all_sites[site].get("website")]

    # Add the always-exposed sites (if configured / known)
    for s in sorted(_ALWAYS_EXPOSE_SITES):
        if s not in sites_list:
            sites_list.append(s)

    # Stable ordering helps UI
    sites_list = sorted(set(sites_list))

    return error_handler(
        status_code=status.HTTP_200_OK,
        json_message={
            "supported_sites": sites_list,
        },
    )


@router.get("/config")
async def get_site_config():
    return error_handler(
        status_code=status.HTTP_200_OK,
        json_message=sites_config
    )
