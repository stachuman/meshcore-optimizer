"""
Configuration, credentials, and discovery state management.
"""

import json
import os
from dataclasses import dataclass, field
from datetime import datetime

from meshcore_optimizer.constants import DEFAULT_GUEST_PASSWORDS


# ---------------------------------------------------------------------------
# Password / access configuration
# ---------------------------------------------------------------------------

@dataclass
class RepeaterAccess:
    """Access credentials for a repeater."""
    prefix: str           # node prefix (or name for matching)
    level: str            # "admin", "guest"
    password: str         # password (empty string = blank password)
    name: str = ""        # optional name for matching


def load_passwords(filename: str) -> tuple[list[RepeaterAccess], list[str]]:
    """Load passwords from JSON file."""
    with open(filename) as f:
        data = json.load(f)

    entries = []
    for item in data.get("passwords", []):
        entries.append(RepeaterAccess(
            prefix=item.get("prefix", "").upper(),
            level=item.get("level", "guest"),
            password=item.get("password", ""),
            name=item.get("name", ""),
        ))

    guest_pws = data.get("default_guest_passwords", DEFAULT_GUEST_PASSWORDS)
    return entries, guest_pws


def match_passwords(node,
                    passwords: list[RepeaterAccess],
                    default_guest_passwords: list[str] = None
                    ) -> list[RepeaterAccess]:
    """
    Find matching password entries for a node, ordered by priority:
      1. Exact prefix match
      2. Name match
      3. Wildcard match
      4. Default guest passwords
    """
    if default_guest_passwords is None:
        default_guest_passwords = DEFAULT_GUEST_PASSWORDS

    results = []
    seen = set()

    for pw in passwords:
        if pw.prefix and pw.prefix == node.prefix and pw.password not in seen:
            results.append(pw)
            seen.add(pw.password)

    for pw in passwords:
        if pw.name and pw.name != "*" and pw.name.lower() in node.name.lower():
            if pw.password not in seen:
                results.append(pw)
                seen.add(pw.password)

    for pw in passwords:
        if pw.name == "*" and pw.password not in seen:
            results.append(pw)
            seen.add(pw.password)

    for gpw in default_guest_passwords:
        if gpw not in seen:
            results.append(RepeaterAccess(
                prefix=node.prefix, level="guest", password=gpw,
                name=f"default({'blank' if gpw == '' else gpw})",
            ))
            seen.add(gpw)

    return results


# ---------------------------------------------------------------------------
# Config file
# ---------------------------------------------------------------------------

@dataclass
class RadioConfig:
    """Radio connection configuration."""
    protocol: str = "tcp"
    host: str = ""
    port: int = 5000
    serial_port: str = ""
    baudrate: int = 115200
    ble_address: str = ""
    meshcore_cli: str = ""


@dataclass
class Config:
    """Full application configuration."""
    radio: RadioConfig = None
    companion_prefix: str = ""
    discovery_max_rounds: int = 5
    discovery_timeout: float = 30.0
    discovery_delay: float = 5.0
    discovery_infer_penalty: float = 5.0
    discovery_save_file: str = "topology.json"
    discovery_hop_penalty: float = 1.0
    discovery_probe_distance_km: float = 2.0
    discovery_probe_min_snr: float = -5.0
    passwords: list = None
    default_guest_passwords: list = None
    health_penalties: dict = None

    def __post_init__(self):
        if self.radio is None:
            self.radio = RadioConfig()
        if self.passwords is None:
            self.passwords = []
        if self.default_guest_passwords is None:
            self.default_guest_passwords = list(DEFAULT_GUEST_PASSWORDS)


def load_config(filename: str) -> Config:
    """Load configuration from JSON file."""
    with open(filename) as f:
        data = json.load(f)

    radio_data = data.get("radio", {})
    radio = RadioConfig(
        protocol=radio_data.get("protocol", "tcp"),
        host=radio_data.get("host", ""),
        port=radio_data.get("port", 5000),
        serial_port=radio_data.get("serial_port", ""),
        baudrate=radio_data.get("baudrate", 115200),
        ble_address=radio_data.get("ble_address", ""),
        meshcore_cli=radio_data.get("meshcore_cli", ""),
    )

    disc = data.get("discovery", {})
    pw_entries = []
    for item in data.get("passwords", []):
        pw_entries.append(RepeaterAccess(
            prefix=item.get("prefix", "").upper(),
            level=item.get("level", "guest"),
            password=item.get("password", ""),
            name=item.get("name", ""),
        ))

    health_penalties = data.get("health_penalties", None)

    # Apply health weights globally so RepeaterNode.health_penalty uses them
    from meshcore_optimizer.topology import set_health_weights
    set_health_weights(health_penalties)

    return Config(
        radio=radio,
        companion_prefix=data.get("companion_prefix", "").upper(),
        discovery_max_rounds=disc.get("max_rounds", 5),
        discovery_timeout=disc.get("timeout", 30.0),
        discovery_delay=disc.get("delay", 5.0),
        discovery_infer_penalty=disc.get("infer_penalty", 5.0),
        discovery_save_file=disc.get("save_file", "topology.json"),
        discovery_hop_penalty=disc.get("hop_penalty", 1.0),
        discovery_probe_distance_km=disc.get("probe_distance_km", 2.0),
        discovery_probe_min_snr=disc.get("probe_min_snr", -5.0),
        passwords=pw_entries,
        default_guest_passwords=data.get("default_guest_passwords",
                                         DEFAULT_GUEST_PASSWORDS),
        health_penalties=health_penalties,
    )


def save_config(config: Config, filename: str):
    """Save configuration to JSON file."""
    radio = {"protocol": config.radio.protocol}
    if config.radio.host:
        radio["host"] = config.radio.host
        radio["port"] = config.radio.port
    if config.radio.serial_port:
        radio["serial_port"] = config.radio.serial_port
        radio["baudrate"] = config.radio.baudrate
    if config.radio.ble_address:
        radio["ble_address"] = config.radio.ble_address
    if config.radio.meshcore_cli:
        radio["meshcore_cli"] = config.radio.meshcore_cli

    data = {
        "radio": radio,
        "companion_prefix": config.companion_prefix,
        "discovery": {
            "max_rounds": config.discovery_max_rounds,
            "timeout": config.discovery_timeout,
            "delay": config.discovery_delay,
            "infer_penalty": config.discovery_infer_penalty,
            "save_file": config.discovery_save_file,
        },
        "passwords": [
            {"name": pw.name, "prefix": pw.prefix,
             "level": pw.level, "password": pw.password}
            for pw in config.passwords
        ],
        "default_guest_passwords": config.default_guest_passwords,
    }

    with open(filename, 'w') as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# Discovery state persistence
# ---------------------------------------------------------------------------

@dataclass
class DiscoveryState:
    """Persisted discovery progress — allows resume after stop/restart."""
    companion_prefix: str = ""
    traced_set: set = field(default_factory=set)
    logged_in_set: set = field(default_factory=set)
    current_round: int = 0
    completed: bool = False

    def save(self, filename: str):
        data = {
            "companion_prefix": self.companion_prefix,
            "traced": sorted(self.traced_set),
            "logged_in": sorted(self.logged_in_set),
            "current_round": self.current_round,
            "completed": self.completed,
            "timestamp": datetime.now().isoformat(timespec='seconds'),
        }
        with open(filename, 'w') as f:
            json.dump(data, f, indent=2)

    @classmethod
    def load(cls, filename: str) -> 'DiscoveryState':
        with open(filename) as f:
            data = json.load(f)
        return cls(
            companion_prefix=data.get("companion_prefix", ""),
            traced_set=set(data.get("traced", [])),
            logged_in_set=set(data.get("logged_in", [])),
            current_round=data.get("current_round", 0),
            completed=data.get("completed", False),
        )


def state_file_for(save_file: str) -> str:
    """Derive discovery state filename from topology filename."""
    base, ext = os.path.splitext(save_file or "topology.json")
    return f"{base}_discovery_state{ext}"
