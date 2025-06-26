# haTUI - Home Assistant Terminal User Interface

A terminal user interface (TUI) for Home Assistant that displays a grid of sensor data and switch/light entities.

![screenshot](/screenshot.png)

~ ~ ~ ~ le first and shittest releaseo ~ ~ ~ ~

TODO: multiple dashboards, graphs, meters, other fancy shit, automations, plugins, email client, kitchen sink. Don't hold your breath. I'm most likely to do the thing that looks the coolest first.

Written in Python with Urwid.

## Features

- **Grid Display**: It's a display with more gridness than a non-grid display could ever match
- **Sensor Management**: Add, delete, and reorder sensors inside the TUI if you don't like editing config files
- **Custom Names and Units**: Set friendly display names for entities so you're not stuck looking at unnamed_getcelldata_average_cell_voltage
- **Menu System**: Hotkeyed drop-down menus, like DOSSHELL!

## Installation

Clone this repo, cd to it, and run:
```bash
pip install -e .
```

## Usage

### First Run

You'll need a long-lived access token from your HA instance:  

## Getting a Home Assistant access token

1. Go to your Home Assistant profile page
2. Scroll down to "Long-Lived Access Tokens"
3. Create a new token
4. Copy the token and get ready to paste it in when prompted by haTUI's setup wizard

## Then:
1. Run  
```bash
~# hatui
```
and enter your Home Assistant URL and token when prompted. The program will create and maintain a `~/.config/hatui-config.yaml` file with your settings  

### Navigation

- **Arrow Keys**: Navigate the sensor grid
- **Enter**: Grab an entity for moving, editing, or sending explicit ON/OFF actions
- **ESC**: Ungrab selection/hide cursor
- **E**: Open Entities menu
- **C**: Open Command menu
- **T**: Toggle any highlighted (but not grabbed) switch or light
- **N**: Send an explicit ON to any **grabbed**  switch or light
- **F**: Send an explicit OFF to any  **grabbed**  switch or light
- **Q**: Bail out

The toggle hotkey works with the cursor over the switch to be controlled, but any other actions require  
**"grabbing"**

You may find somewhat unintuitive the idea of "grabbing" an entity to perform actions on it.  
In addition to needing to grab it to move it around the grid, you also need to grab it to send explicit  
actions or rename/set unit. This made sense to me at the time, and I can't be bothered changing it now.

### Configuration

The program looks for a `hatui-config.yaml` file in the following locations:
1. `$HOME/.config/hatui-config.yaml`
2. `/etc/hatui-config.yaml`
3. `./hatui-config.yaml` (cwd)

If none of these exist, it will create and maintain a file at `$HOME/.config/hatui-config.yaml`.

Example config (but you can also look at the actual config example file provided in the repo):

```yaml
ha_url: http://your-home-assistant:8123
ha_token: your-long-lived-access-token
entities:
  - sensor.example_sensor_1
  - sensor.example_sensor_2
display_names:
  sensor.example_sensor_1: "Friendly Name"
units:
  sensor.example_sensor_1: "W"
```

Nota bene: haTUI hammers updates into this file in realtime whenever you make a change to the entities within the UI. If you're planning to edit the config directly, exit haTUI first so you don't end up fighting the program.

haTUI...I'm sure there's a hawk tuah joke in here somewhere.
