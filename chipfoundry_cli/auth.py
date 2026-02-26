"""
Browser-based OAuth login flow for the CLI.

Implements the localhost callback pattern:
1. Start a temporary HTTP server on a random port.
2. Open the Cognito hosted UI in the user's browser.
3. Receive the callback with the authorization code.
4. Exchange the code for a transient Cognito token.
5. Use that token to generate a long-lived API key.
"""

import hashlib
import base64
import secrets
import threading
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from typing import Optional, Tuple

from chipfoundry_cli.api import PlatformAPI, PlatformAPIError

_LOGIN_SUCCESS_HTML = """<!DOCTYPE html>
<html>
<head><title>ChipFoundry CLI</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         display: flex; align-items: center; justify-content: center;
         min-height: 100vh; margin: 0; background: #f8fafc; color: #1e293b; }
  .card { text-align: center; padding: 2rem; }
  h1 { font-size: 1.5rem; margin-bottom: 0.5rem; }
  p { color: #64748b; }
</style>
</head>
<body><div class="card">
  <h1>Login successful!</h1>
  <p>You can close this tab and return to the terminal.</p>
</div></body></html>"""

_LOGIN_ERROR_HTML = """<!DOCTYPE html>
<html>
<head><title>ChipFoundry CLI</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         display: flex; align-items: center; justify-content: center;
         min-height: 100vh; margin: 0; background: #fef2f2; color: #991b1b; }
  .card { text-align: center; padding: 2rem; }
  h1 { font-size: 1.5rem; margin-bottom: 0.5rem; }
  p { color: #b91c1c; }
</style>
</head>
<body><div class="card">
  <h1>Login failed</h1>
  <p>{error}</p>
</div></body></html>"""


def _generate_pkce() -> Tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge (S256)."""
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return code_verifier, code_challenge


class _CallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler that captures the OAuth callback."""

    auth_code: Optional[str] = None
    auth_state: Optional[str] = None
    error: Optional[str] = None

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        error = params.get("error")
        if error:
            _CallbackHandler.error = error[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                _LOGIN_ERROR_HTML.format(error=error[0]).encode("utf-8")
            )
        else:
            _CallbackHandler.auth_code = (params.get("code") or [None])[0]
            _CallbackHandler.auth_state = (params.get("state") or [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(_LOGIN_SUCCESS_HTML.encode("utf-8"))

        # Shut down the server after handling the callback
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def log_message(self, format, *args):
        pass  # suppress request logging


def browser_login(api_url: str) -> str:
    """
    Run the full browser-based login flow.

    Returns the plaintext API key (cfk_live_...) on success.
    Raises PlatformAPIError on failure.
    """
    api = PlatformAPI(api_url=api_url, api_key="")

    # Start a temporary server on a random port
    server = HTTPServer(("127.0.0.1", 0), _CallbackHandler)
    port = server.server_address[1]
    redirect_uri = f"http://localhost:{port}/callback"

    # Get the authorize URL from the backend (includes Cognito domain, client_id)
    try:
        auth_info = api.get_authorize_url()
    except PlatformAPIError as e:
        raise PlatformAPIError(f"Could not get authorization URL: {e}")

    authorize_url: str = auth_info["authorization_url"]
    backend_code_verifier: str = auth_info.get("code_verifier", "")

    # Generate our own PKCE pair for the CLI redirect
    code_verifier, code_challenge = _generate_pkce()

    # Rebuild the authorize URL to use our localhost redirect and PKCE
    from urllib.parse import urlencode, urlparse as _urlparse, parse_qs as _parse_qs

    parsed_auth = _urlparse(authorize_url)
    auth_params = {k: v[0] for k, v in _parse_qs(parsed_auth.query).items()}
    state = secrets.token_urlsafe(32)
    auth_params.update({
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    })
    final_url = f"{parsed_auth.scheme}://{parsed_auth.netloc}{parsed_auth.path}?{urlencode(auth_params)}"

    # Reset handler state
    _CallbackHandler.auth_code = None
    _CallbackHandler.auth_state = None
    _CallbackHandler.error = None

    # Open browser and wait for callback
    webbrowser.open(final_url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
        raise PlatformAPIError("Login cancelled.")
    finally:
        server.server_close()

    if _CallbackHandler.error:
        raise PlatformAPIError(f"Authentication failed: {_CallbackHandler.error}")

    auth_code = _CallbackHandler.auth_code
    if not auth_code:
        raise PlatformAPIError("No authorization code received.")

    callback_state = _CallbackHandler.auth_state
    if callback_state != state:
        raise PlatformAPIError("State mismatch — possible CSRF attack. Login aborted.")

    # Exchange code for tokens via the backend
    try:
        token_data = api.exchange_portal_code(
            code=auth_code,
            code_verifier=code_verifier,
            redirect_uri=redirect_uri,
            state=state,
        )
    except PlatformAPIError:
        raise
    except Exception as e:
        raise PlatformAPIError(f"Token exchange failed: {e}")

    access_token = token_data.get("access_token")
    if not access_token:
        raise PlatformAPIError("No access token in response.")

    # Use the transient access token to generate a long-lived API key
    try:
        key_data = api.generate_api_key(cognito_token=access_token)
    except PlatformAPIError:
        raise
    except Exception as e:
        raise PlatformAPIError(f"API key generation failed: {e}")

    api_key = key_data.get("api_key")
    if not api_key:
        raise PlatformAPIError("No API key in response.")

    return api_key
