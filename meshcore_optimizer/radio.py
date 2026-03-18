"""
Shared radio communication helpers.

Used by discovery, web UI, and node commands.
"""

import asyncio
from datetime import datetime

from meshcore_optimizer.constants import (
    PATH_HASH_MODE, HOP_HEX_LEN,
    MIN_LOGIN_TIMEOUT_S,
)


async def connect_radio(config):
    """Create a MeshCore connection from radio config."""
    from meshcore import MeshCore

    if config.protocol == "tcp":
        if not config.host:
            raise ValueError("TCP host not configured")
        print(f"  Connecting via TCP to {config.host}:{config.port}...")
        mc = await MeshCore.create_tcp(host=config.host, port=config.port)
    elif config.protocol == "serial":
        if not config.serial_port:
            raise ValueError("Serial port not configured")
        print(f"  Connecting via serial {config.serial_port}...")
        mc = await MeshCore.create_serial(
            port=config.serial_port, baudrate=config.baudrate)
    elif config.protocol == "ble":
        addr = config.ble_address or None
        print(f"  Connecting via BLE{' to ' + addr if addr else ''}...")
        mc = await MeshCore.create_ble(address=addr)
    else:
        raise ValueError(f"Unknown protocol: {config.protocol}")

    if mc is None:
        raise ConnectionError(f"Failed to connect via {config.protocol}")
    return mc


def find_contact(mc, prefix):
    """Find a contact dict by node prefix in mc.contacts."""
    prefix = prefix.upper()
    for pub_key, ct in mc.contacts.items():
        if not isinstance(ct, dict):
            continue
        if pub_key[:8].upper() == prefix:
            return ct
    return None


async def set_contact_path(mc, contact, path_result):
    """Set routing path on a contact from a PathResult."""
    if not path_result.found:
        print(f"    Route: no path found")
        return

    # Log route with names and prefixes
    hops_display = " -> ".join(
        f"{n} [{p[:4]}]"
        for n, p in zip(path_result.path_names, path_result.path))
    print(f"    Route: {hops_display} "
          f"({path_result.bottleneck_snr:+.1f} dB, "
          f"{path_result.hop_count} hops)")

    if not contact or path_result.hop_count == 0:
        return

    hops = path_result.path[:-1]
    path_hex = "".join(p[:HOP_HEX_LEN].lower() for p in hops)
    try:
        await mc.commands.change_contact_path(
            contact, path_hex, path_hash_mode=PATH_HASH_MODE)
    except Exception as e:
        print(f"    Could not set path: {e}")


async def login_to_node(mc, contact, node_name, password, timeout,
                        max_wait=None):
    """
    Login to a repeater. Returns (success, error_msg).
    Handles subscribe-before-send pattern to avoid race conditions.
    max_wait caps the login response wait time (useful for interactive
    single-node commands where you don't want to wait 60s+).
    """
    from meshcore import EventType

    pw_display = f"'{password}'" if password else "(blank)"

    login_future = asyncio.Future()

    def _on_login(event):
        if not login_future.done():
            login_future.set_result(event)

    login_sub = mc.subscribe(EventType.LOGIN_SUCCESS, _on_login)

    try:
        print(f"      TX: Sending login ({pw_display}) to {node_name}...")
        login_result = await asyncio.wait_for(
            mc.commands.send_login(contact, password), timeout=timeout)
    except Exception as e:
        login_sub.unsubscribe()
        return False, f"login send error: {e}"

    if login_result.type == EventType.ERROR:
        login_sub.unsubscribe()
        reason = login_result.payload.get("reason", "")
        if reason == "no_event_received":
            return False, "CONNECTION_LOST"
        return False, f"login rejected ({pw_display})"

    # Wait for LOGIN_SUCCESS over the air
    # Divide by 800 (not 1000) to give ~25% extra margin over the
    # suggested timeout (which is in milliseconds).
    login_timeout = login_result.payload.get("suggested_timeout", 0) / 800
    if isinstance(contact, dict) and contact.get("timeout", 0) != 0:
        login_timeout = contact["timeout"]
    login_timeout = max(login_timeout, MIN_LOGIN_TIMEOUT_S)
    if max_wait:
        login_timeout = min(login_timeout, max_wait)

    print(f"      ... waiting for login response "
          f"(timeout={login_timeout:.0f}s)...")
    try:
        login_event = await asyncio.wait_for(
            login_future, timeout=login_timeout)
    except asyncio.TimeoutError:
        login_event = None
    finally:
        login_sub.unsubscribe()

    if login_event is None:
        return False, f"login timeout ({pw_display})"
    if login_event.type == EventType.LOGIN_SUCCESS:
        print(f"      RX: Login SUCCESS with {pw_display}")
        return True, ""
    return False, f"login failed ({pw_display})"


async def fetch_status(mc, contact, node, timeout):
    """
    Fetch status from a logged-in node with one retry.
    Updates node.status and node.status_timestamp on success.
    Returns the status dict or None.
    """
    status_data = None
    for attempt in (1, 2):
        try:
            print(f"      TX: Requesting status from {node.name}"
                  f" (attempt {attempt}/2)...")
            status_data = await mc.commands.req_status_sync(
                contact, min_timeout=timeout)
            if status_data:
                break
            print(f"      RX: No status data")
        except Exception as e:
            print(f"      RX: Status error: {e}")
        if attempt < 2:
            await asyncio.sleep(2)

    if status_data:
        node.status = status_data
        node.status_timestamp = datetime.now().isoformat(timespec='seconds')
        bat = status_data.get('bat', 0)
        tx_q = status_data.get('tx_queue_len', 0)
        full = status_data.get('full_evts', 0)
        uptime_h = status_data.get('uptime', 0) / 3600
        print(f"      RX: Status — bat:{bat}mV  txq:{tx_q}  "
              f"full_evts:{full}  uptime:{uptime_h:.1f}h")

    return status_data
