"""
Platform API client for communicating with the chipIgnite backend.

All CLI commands that need platform data go through this module.
Authentication is via a long-lived API key (cfk_live_...) stored in config.toml.
"""

import httpx
from typing import Any, Dict, List, Optional


class PlatformAPIError(Exception):
    """Raised when a platform API call fails."""

    def __init__(self, message: str, status_code: int = 0):
        self.status_code = status_code
        super().__init__(message)


class PlatformAPI:
    """Thin wrapper around httpx for authenticated platform API calls."""

    def __init__(self, api_url: str, api_key: str, timeout: float = 30.0):
        self._base = api_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def _url(self, path: str) -> str:
        return f"{self._base}/api/v1{path}"

    def _handle_response(self, resp: httpx.Response) -> Any:
        if resp.status_code == 401:
            raise PlatformAPIError(
                "Your API key is no longer valid. Run `cf login` to generate a new one.",
                status_code=401,
            )
        if resp.status_code == 403:
            raise PlatformAPIError(
                "You do not have permission to perform this action.",
                status_code=403,
            )
        if resp.status_code == 404:
            raise PlatformAPIError(
                "Resource not found. It may have been deleted or you may not have access.",
                status_code=404,
            )
        if resp.status_code >= 400:
            detail = ""
            try:
                body = resp.json()
                detail = body.get("error") or body.get("detail") or str(body)
            except Exception:
                detail = resp.text[:200]
            raise PlatformAPIError(
                f"API error ({resp.status_code}): {detail}",
                status_code=resp.status_code,
            )
        return resp.json()

    # --- Auth ---

    def validate(self) -> Dict[str, Any]:
        """Validate the API key and return user info."""
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.get(self._url("/auth/validate"), headers=self._headers())
            return self._handle_response(resp)

    def generate_api_key(self, cognito_token: str) -> Dict[str, Any]:
        """
        Generate a CLI API key using a transient Cognito access token.
        This is called during `cf login` — not with the stored API key.
        """
        headers = {"Authorization": f"Bearer {cognito_token}"}
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.post(self._url("/auth/cli/api-key"), headers=headers)
            return self._handle_response(resp)

    def revoke_api_key(self) -> Dict[str, Any]:
        """Revoke the current API key (used by `cf logout`)."""
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.delete(self._url("/auth/cli/api-key"), headers=self._headers())
            return self._handle_response(resp)

    def exchange_portal_code(
        self,
        code: str,
        code_verifier: str,
        redirect_uri: str,
        state: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Exchange an OAuth authorization code for tokens via the portal callback."""
        payload: Dict[str, Any] = {
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
        }
        if state:
            payload["state"] = state
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.post(
                self._url("/auth/portal/callback"),
                json=payload,
            )
            return self._handle_response(resp)

    def get_authorize_url(self) -> Dict[str, Any]:
        """Get the Cognito OAuth authorization URL for the portal."""
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.get(self._url("/auth/portal/authorize"))
            return self._handle_response(resp)

    # --- Projects ---

    def create_project(
        self,
        name: str,
        shuttle_id: Optional[str] = None,
        description: Optional[str] = None,
        design_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a new project on the platform."""
        payload: Dict[str, Any] = {
            "name": name,
            "registration_source": "cli",
        }
        if shuttle_id:
            payload["shuttle_id"] = shuttle_id
        if description:
            payload["description"] = description
        if design_type:
            payload["design_type"] = design_type
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.post(
                self._url("/projects"),
                json=payload,
                headers=self._headers(),
            )
            return self._handle_response(resp)

    def get_project(self, project_id: str) -> Dict[str, Any]:
        """Get a project by ID."""
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.get(
                self._url(f"/projects/{project_id}"),
                headers=self._headers(),
            )
            return self._handle_response(resp)

    def list_my_projects(self) -> List[Dict[str, Any]]:
        """List all projects belonging to the authenticated user."""
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.get(
                self._url("/projects/me"),
                headers=self._headers(),
            )
            return self._handle_response(resp)

    def update_project(self, project_id: str, **fields: Any) -> Dict[str, Any]:
        """Update a project on the platform."""
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.put(
                self._url(f"/projects/{project_id}"),
                json=fields,
                headers=self._headers(),
            )
            return self._handle_response(resp)

    # --- Shuttles ---

    def list_shuttles(self) -> List[Dict[str, Any]]:
        """List shuttles available for submission."""
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.get(
                self._url("/shuttles/available"),
                headers=self._headers(),
            )
            return self._handle_response(resp)
