from __future__ import annotations

import httpx

from app.models import RouteResult
from app.services.tls import build_ssl_context, is_certificate_verify_error


class OsrmError(Exception):
    """Raised when an OSRM API call fails for any reason."""


class OsrmClient:
    """
    Thin wrapper around the OSRM routing API.

    IMPORTANT — coordinate order:
    - Request URL path : lon,lat  (OSRM convention)
    - Response GeoJSON : [lon, lat]  (preserved as-is → RouteResult)
    - Folium / Shapely : caller's responsibility to reorder
    """

    def __init__(
        self,
        base_url: str,
        profile: str = "foot",
        timeout_seconds: float = 10.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._profile = profile
        self._timeout = timeout_seconds
        self._cache: dict[tuple[float, float, float, float, str], RouteResult] = {}
        self._ssl_context = build_ssl_context()

    def route(
        self,
        from_lat: float,
        from_lon: float,
        to_lat: float,
        to_lon: float,
    ) -> RouteResult:
        """
        Request a walking route from OSRM.

        URL format: /route/v1/{profile}/{from_lon},{from_lat};{to_lon},{to_lat}
        Returns RouteResult with coordinates in [lon, lat] GeoJSON order.
        Raises OsrmError on any failure.
        """
        cache_key = (from_lat, from_lon, to_lat, to_lon, self._profile)
        if cache_key in self._cache:
            return self._cache[cache_key]

        # OSRM path uses lon,lat order
        url = (
            f"{self._base_url}/route/v1/{self._profile}/"
            f"{from_lon},{from_lat};{to_lon},{to_lat}"
        )
        params = {
            "overview": "full",
            "geometries": "geojson",
            "steps": "false",
            "annotations": "false",
        }

        try:
            with httpx.Client(timeout=self._timeout, verify=self._ssl_context) as client:
                resp = client.get(url, params=params)
                resp.raise_for_status()
                data: dict = resp.json()
        except httpx.TimeoutException as exc:
            raise OsrmError("OSRM request timed out") from exc
        except httpx.HTTPStatusError as exc:
            raise OsrmError(
                f"OSRM HTTP error {exc.response.status_code}"
            ) from exc
        except httpx.RequestError as exc:
            if is_certificate_verify_error(exc):
                raise OsrmError(
                    "SSL 憑證驗證失敗，請更新 certifi/truststore 或確認系統根憑證"
                ) from exc
            raise OsrmError(f"OSRM request error: {exc}") from exc
        except (ValueError, KeyError) as exc:
            raise OsrmError(f"OSRM response parse error: {exc}") from exc

        osrm_code: str = data.get("code", "")
        if osrm_code != "Ok":
            if osrm_code in {"ProfileNotFound", "InvalidService", "InvalidVersion"}:
                raise OsrmError(
                    "OSRM walking profile 設定可能不正確，請檢查 OSRM_PROFILE"
                )
            if osrm_code in {"NoRoute", "NoSegment"}:
                raise OsrmError("找不到步行路線")
            raise OsrmError(f"OSRM error code: {osrm_code!r}")

        routes: list[dict] = data.get("routes", [])
        if not routes:
            raise OsrmError("找不到步行路線")

        route = routes[0]
        coordinates: list[list[float]] = (
            route.get("geometry", {}).get("coordinates", [])
        )
        if len(coordinates) < 2:
            raise OsrmError(
                "OSRM route geometry has fewer than 2 coordinates"
            )

        result = RouteResult(
            # GeoJSON coordinates are already [lon, lat] — keep as-is
            coordinates_lonlat=coordinates,
            distance_m=float(route["distance"]),
            duration_s=float(route["duration"]),
        )
        self._cache[cache_key] = result
        return result
