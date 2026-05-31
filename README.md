# NVIDIA Shield Remote

Home Assistant custom integration for the NVIDIA Shield TV remote protocol.

This integration talks to the Shield remote service directly over the local
network. It is intended for dashboards, automations, and projector workflows
where the Shield should wake from standby without Wake-on-LAN.

## Features

- mDNS discovery for Shield devices exposing `_nv_shield_remote._tcp.local.`
- Manual host and port setup
- Home Assistant `remote` entity
- First-time PIN pairing in the Home Assistant setup flow
- Wake using NVIDIA's non-toggle power-on command
- Core remote buttons: d-pad, select, back, home, play/pause, volume, mute

## Installation With HACS

1. Open HACS.
2. Add this repository as a custom repository with category `Integration`.
3. Install `NVIDIA Shield Remote`.
4. Restart Home Assistant.
5. Add the integration from `Settings > Devices & services`.

## Pairing

Pairing happens while adding the integration. Keep the Shield display visible,
start the discovered or manual setup flow, and enter the PIN that appears on
screen.

The generated client certificate and key are stored in Home Assistant's config
entry storage. Do not copy them into YAML or publish them.

## Example Commands

Wake the Shield:

```yaml
service: remote.turn_on
target:
  entity_id: remote.nvidia_shield
```

Send a button:

```yaml
service: remote.send_command
target:
  entity_id: remote.nvidia_shield
data:
  command: HOME
```

Supported commands:

```text
UP, DOWN, LEFT, RIGHT, SELECT, BACK, HOME, MENU, PLAY_PAUSE,
REWIND, FAST_FORWARD, VOLUME_UP, VOLUME_DOWN, MUTE, POWER, POWERON
```

## Projector And Dashboard Examples

Example package and dashboard snippets are in `examples/`.

The projector package assumes:

- `switch.projector`
- `remote.nvidia_shield`

Adjust entity IDs after setup if Home Assistant assigns a different remote
entity ID.

## Notes

- The Shield protocol uses TLS on TCP port `8987`.
- Wake is implemented with NVIDIA's non-toggle power-on command.
- Standby currently uses the Shield power command.
- The first release focuses on core remote control; app inventory and keyboard
  entry are not implemented yet.
