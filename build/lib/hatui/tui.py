import urwid
import requests
from datetime import datetime
from .ha import HA, HAApiError
import threading
import time

DEBUG_LOGGING = False  # if set TRUE, this will dump a hatui_debug.log in the cwd with all keypresses & actions

class MenuItem:
    def __init__(self, label, action=None, submenu=None, enabled=True):
        self.label = label
        self.action = action
        self.submenu = submenu
        self.enabled = enabled


class DropDownMenu:
    def __init__(self, label, items):
        self.label = label
        self.items = items
        self.is_open = False


class SmartFooter(urwid.WidgetWrap):
    def __init__(self, left_widget, right_widget):
        self.left_widget = left_widget
        self.right_widget = right_widget
        super().__init__(self._build())

    def _build(self, size=(80,)):
        maxcol = size[0] if size else 80
        left_text = self.left_widget.get_text()[0]
        right_text = self.right_widget.get_text()[0]
        gap = maxcol - len(left_text) - len(right_text)
        if gap >= 1:
            line = left_text + ' ' * gap + right_text
            return urwid.Text(line)
        else:
            return urwid.Pile([
                urwid.Text(left_text),
                urwid.Text(right_text, align='right'),
            ])

    def render(self, size, focus=False):
        self._w = self._build(size)
        return self._w.render(size, focus)

    def selectable(self):
        return False

    def keypress(self, size, key):
        return key


class TUI:
    def __init__(self, config):
        self.config = config
        self.editing = False
        self.ha = None
        self.entities = []
        self.loop = None
        self.debug_messages = []
        self._pending_entity_states = {}  # entity_id -> 'on'/'off' for immediate UI feedback
        self._entity_state_cache = {}     # entity_id -> state dict
        self._entity_state_cache_time = 0 # timestamp of last cache refresh
        self._entity_state_cache_ttl = 2  # seconds to keep cache valid
        
        self.cursor_x = 0
        self.cursor_y = 0
        self.selected_entity_index = None
        self.grid_width = 3  # Number of columns in the grid. 3 is pretty good for most use cases but go fuckin nuts, I ain't yo mama. 5 looks nice too
        self.cursor_visible = False  # Don't show cursor until user moves it! CLEAN
        
        self.active_menu = None
        self.menu_index = 0
        
        #~/.config/hatui-config.yaml (or /etc/hatui-config.yaml) contains all config for this program: see example.
        # You can configure the Entities (sensors, switches, or lights) there, or you can use the menus in the TUI.
        # The config file is updated IN REALTIME so make sure to exit haTUI if you're munging config manually.
        # We also support configuring non-gibberish names and meaningful units for your edification.
        # Custom display names for entities
        self.display_names = self.config.data.get("display_names", {})
        # Custom units for entities
        self.units = self.config.data.get("units", {})

        self.create_menus()
        self.header = urwid.Text("haTUI - Home Assistant TUI", align='center')
        self.menu_bar = self.build_menu_bar()
        self.footer_help = urwid.Text("", align='left', wrap='clip')
        self.footer_right = urwid.Text("", align='right', wrap='clip')
        self.footer = urwid.AttrMap(urwid.Columns([
            ('weight', 1, self.footer_help),
            ('pack', self.footer_right),
        ]), 'footer')
        self._footer_entity_count = 0
        self._update_context_help()
        
        initial_body = urwid.Filler(urwid.Text("Initializing..."))
        self.main_widget = urwid.Frame(
            body=initial_body,
            header=urwid.Pile([
                urwid.AttrMap(self.header, 'header'),
                self.menu_bar
            ]),
            footer=self.footer
        )
        self.log_debug("TUI Initialized")

    def create_menus(self):
        self.menus = [
            DropDownMenu("[E]ntities", [
                MenuItem([('underline', 'A'), 'dd Entity'], action=self.prompt_for_entity),
                MenuItem([('underline', 'R'), 'ename Entity'], action=self.prompt_for_rename, enabled=False),
                MenuItem([' Set ', ('underline', 'U'), 'nit'], action=self.prompt_for_unit, enabled=False),
                MenuItem([('underline', 'D'), 'elete Entity'], action=self.delete_selected_entity, enabled=False),
            ]),
            DropDownMenu("[C]ommand", [
                MenuItem('On', action=self.menu_on_switch, enabled=False),
                MenuItem('Off', action=self.menu_off_switch, enabled=False),
            ]),
        ]

    def build_menu_bar(self):
        menu_widgets = []
        for i, menu in enumerate(self.menus):
            menu_widget = urwid.Text(menu.label)
            if i == self.menu_index and self.active_menu == i:
                menu_widget = urwid.AttrMap(menu_widget, 'selected')
            elif i == self.active_menu:
                menu_widget = urwid.AttrMap(menu_widget, 'menu')
            menu_widgets.append(menu_widget)
        menu_widgets.append(urwid.Text("[Q]uit", align='right'))
        menu_bar = urwid.Columns(menu_widgets, dividechars=1)
        
        separator = urwid.AttrMap(urwid.Divider('_'), 'menu')
        menu_with_separator = urwid.Pile([menu_bar, separator])
        
        if self.active_menu is not None:
            menu = self.menus[self.active_menu]
            menu_items = []
            menu_header = urwid.AttrMap(urwid.Text(["┌─ "] + ([('underline', l[1]), l[2:]] if isinstance(menu.label, list) and len(menu.label) > 1 and menu.label[0] == '[' else [menu.label]) + [" Menu ─" + "─" * 20]), 'selected')
            menu_items.append(menu_header)
            
            if self.active_menu == 0:
                entity_selected = self.selected_entity_index is not None
                menu.items[1].enabled = entity_selected
                menu.items[2].enabled = entity_selected
                menu.items[3].enabled = entity_selected
                if not entity_selected:
                    menu.items[1].label = [('underline', 'R'), 'ename (must grab entity with [Enter] first)']
                    menu.items[2].label = ['Set ', ('underline', 'U'), 'nit (must grab entity with [Enter] first)']
                    menu.items[3].label = [('underline', 'D'), 'elete (must grab entity with [Enter] first)']
                else:
                    menu.items[1].label = [('underline', 'R'), 'ename Entity']
                    menu.items[2].label = ['Set ', ('underline', 'U'), 'nit']
                    menu.items[3].label = [('underline', 'D'), 'elete Entity']
            if self.active_menu == 1:
                entity_selected = self.selected_entity_index is not None
                menu.items[0].enabled = entity_selected
                menu.items[1].enabled = entity_selected
                if not entity_selected:
                    menu.items[0].label = ['Send explicit O', ('underline', 'N'), ' command (must grab entity with [Enter] first)']
                    menu.items[1].label = ['Send explicit O', ('underline', 'F'), 'F command (must grab entity with [Enter] first)']
                else:
                    menu.items[0].label = ['Send explicit O', ('underline', 'N'), ' command']
                    menu.items[1].label = ['Send explicit O', ('underline', 'F'), 'F command']
            for j, item in enumerate(menu.items):
                is_selected = (j == self.menu_index)
                item_widget = urwid.Text(item.label)
                if not item.enabled:
                    item_text = urwid.Text(('disabled', item.label))
                    menu_items.append(urwid.AttrMap(item_text, 'disabled'))
                else:
                    if is_selected:
                        menu_items.append(urwid.AttrMap(item_widget, 'selected'))
                    else:
                        menu_items.append(urwid.AttrMap(item_widget, 'menu'))
            
            hints = urwid.AttrMap(urwid.Text("│ ↑↓: Navigate | Enter: Select | E/C: Switch Menu | ESC: Cancel" + " " * 8), 'menu')
            menu_items.append(urwid.AttrMap(urwid.Text("└" + "─" * 35), 'menu'))
            menu_items.append(hints)
            
            drop_down = urwid.Pile(menu_items)
            return urwid.Pile([menu_with_separator, drop_down])
        
        return menu_with_separator

    def show_menu(self, menu_index):
        if 0 <= menu_index < len(self.menus):
            self.menus[menu_index].is_open = True
            self.active_menu = menu_index
            self.menu_index = 0
            self.update_menu_display()

    def hide_all_menus(self):
        for menu in self.menus:
            menu.is_open = False
        self.active_menu = None
        self.update_menu_display()

    def update_menu_display(self):
        self.menu_bar = self.build_menu_bar()
        if hasattr(self, 'main_widget'):
            self.main_widget.header = urwid.Pile([
                urwid.AttrMap(self.header, 'header'),
                self.menu_bar
            ])
            self.loop.draw_screen()

    def quit_app(self):
        raise urwid.ExitMainLoop()

    def show_status_error(self, message):
        self._error_active = True
        self.footer_help.set_text(message)
        if self.loop:
            def clear_error(loop, user_data):
                self._error_active = False
                self._update_context_help()
            self.loop.set_alarm_in(3, clear_error)
            self.loop.draw_screen()

    def prompt_for_rename(self):
        if self.selected_entity_index is not None:
            entity = self.entities[self.selected_entity_index]
            current_name = self.display_names.get(entity, entity)
            prompt = (f"Rename entity: {entity}\n"
                      f"Current name: {current_name}\n"
                      f"Enter new display name and press [Enter] to confirm:")
            self.log_debug(f"Prompting to rename entity: {entity}")
            self.editing = 'rename'
            self.edit = urwid.Edit(prompt + "\n> ", current_name)
            self.main_widget.body = urwid.Filler(self.edit)
        else:
            self.show_status_error("ERROR: select an entity by grabbing it with [Enter] first!")
            self.log_debug("No entity selected for renaming")

    def save_display_name(self, entity, display_name):
        if display_name.strip():
            self.display_names[entity] = display_name.strip()
        else:
            self.display_names.pop(entity, None)
        
        self.config.data["display_names"] = self.display_names
        self.config.save_config()
        self.log_debug(f"Saved display name for {entity}: {display_name}")
        self.update_ui()

    def log_debug(self, message):
        if DEBUG_LOGGING:
            with open('hatui_debug.log', 'a') as f:
                print(f"[TUI] {message}", file=f)
        self.debug_messages.append(message)
        self._update_context_help()

    def run(self):
        self.log_debug("Starting TUI run loop.")
        if not self.config.data.get("ha_url") or not self.config.data.get("ha_token"):
            self.prompt_for_ha_credentials()
        else:
            self.init_app()
        palette = [
            ('header', 'white', 'dark blue'),
            ('footer', 'white', 'dark blue'),
            ('menu', 'white', 'dark green'),
            ('cursor', 'black', 'yellow'),
            ('selected', 'white', 'dark red'),
            ('switch_on', 'black', 'dark green'),
            ('switch_on_selected', 'white', 'dark red'),
            ('disabled', 'dark gray', ''),
            ('underline', 'underline', ''),
        ]
        self.loop = urwid.MainLoop(
            self.main_widget,
            palette=palette,
            unhandled_input=self.handle_input
        )
        self.loop.set_alarm_in(1, self._update_footer_right)
        self.loop.set_alarm_in(5, self.refresh_entities)
        self.loop.set_alarm_in(2, self._periodic_refresh)
        self.loop.run()

    def is_menu_open(self):
        return self.active_menu is not None

    def is_modal_open(self):
        return bool(self.editing)

    def refresh_entities(self, loop=None, user_data=None):
        if self.is_menu_open() or self.is_modal_open():
            if self.loop:
                self.loop.set_alarm_in(5, self.refresh_entities)
            return
        self.log_debug("Refreshing entity data from Home Assistant.")
        self._pending_entity_states.clear() 
        self._refresh_entity_state_cache()
        self.update_ui()
        if self.loop:
            self.loop.set_alarm_in(5, self.refresh_entities)

    def init_app(self):
        self.log_debug("Initializing main application.")
        try:
            self.ha = HA(self.config.data["ha_url"], self.config.data["ha_token"])
            
            try:
                entities = self.ha.list_entities()
                self.log_debug(f"Found {len(entities)} entities")
                sensor_entities = [e for e in entities if e.startswith('sensor.')]
                self.log_debug(f"Available sensors: {sensor_entities[:5]}...")
            except Exception as e:
                self.log_debug(f"Could not list entities: {e}")
            
            self.entities = self.config.data.get("entities", [])
            self.log_debug(f"Loaded entities from config: {self.entities}")
            
            self.main_widget.body = self.build_grid()
            self.log_debug(f"App initialized. Found {len(self.entities)} entities.")
            if not self.entities:
                self.log_debug("No entities in config, adding dehumidifier sensor")
                self.add_entity("sensor.dehumidifier_energy_voltage")
            else:
                self.log_debug(f"Entities already in config: {self.entities}")
        except Exception as e:
            self.log_debug(f"Error in init_app: {e}")

    def prompt_for_ha_credentials(self):
        self.log_debug("Prompting for HA address.")
        self.editing = 'ha_url'
        self.edit = urwid.Edit("Home Assistant URL: ")
        self.main_widget.body = urwid.Filler(self.edit)

    def handle_input(self, key):
        if DEBUG_LOGGING:
            self.log_debug(f"Key pressed: {repr(key)}")
        
        if self.editing == 'ha_url':
            if key == 'enter':
                self.config.data['ha_url'] = self.edit.edit_text.strip()
                self.editing = 'ha_token'
                self.edit = urwid.Edit("Home Assistant Token: ")
                self.main_widget.body = urwid.Filler(self.edit)
                self.log_debug("Prompting for HA Token.")
            return
        elif self.editing == 'ha_token':
            if key == 'enter':
                self.config.data['ha_token'] = self.edit.edit_text.strip()
                self.config.save_config()
                self.editing = False
                self.log_debug("Credentials saved. Initializing app.")
                self.init_app()
            return

        if self.editing:
            if key == 'enter':
                if self.editing == 'rename':
                    display_name = self.edit.edit_text.strip()
                    if self.selected_entity_index is not None:
                        entity = self.entities[self.selected_entity_index]
                        self.save_display_name(entity, display_name)
                    self.editing = False
                    self.main_widget.body = self.build_grid()
                elif self.editing == 'unit':
                    unit = self.edit.edit_text.strip()
                    if self.selected_entity_index is not None:
                        entity = self.entities[self.selected_entity_index]
                        self.save_unit(entity, unit)
                    self.editing = False
                    self.main_widget.body = self.build_grid()
                else:
                    entity_id = self.edit.edit_text.strip()
                    if entity_id:
                        self.log_debug(f"Adding entity: {entity_id}")
                        self.add_entity(entity_id)
                    self.editing = False
                    self.main_widget.body = self.build_grid()
            elif key == 'esc' or key == 'escape' or key == '\x1b':
                self.editing = False
                self.main_widget.body = self.build_grid()
            return

        # Toggle function
        if key in ('t', 'T', ' '):
            if self.cursor_visible:
                idx = self.get_entity_index_from_position(self.cursor_x, self.cursor_y)
                if 0 <= idx < len(self.entities):
                    entity_id = self.entities[idx]
                    self.log_debug(f"[T/SPACE] Entity under cursor: {entity_id}")
                    if entity_id.startswith('switch.') or entity_id.startswith('light.'):
                        self.log_debug(f"[T/SPACE] Toggling entity: {entity_id}")
                        try:
                            result = self.ha.toggle_entity_ws(entity_id)
                            self.log_debug(f"Toggle result: {result}")
                        except Exception as e:
                            self.log_debug(f"Error toggling entity: {e}")
                        self._refresh_entity_state_cache()
                        self.update_ui()
            return

        # Navigation and menus
        if key == 'e' or key == 'E':
            # Entities menu
            if self.active_menu != 0:
                self.show_menu(0)
            else:
                self.hide_all_menus()
        elif key == 'c' or key == 'C':
            # Command menu
            if self.active_menu != 1:
                self.show_menu(1)
            else:
                self.hide_all_menus()
        elif key == 'up':
            if self.active_menu is not None:
                menu = self.menus[self.active_menu]
                prev_index = self.menu_index
                while self.menu_index > 0:
                    self.menu_index -= 1
                    if menu.items[self.menu_index].enabled:
                        break
                if not menu.items[self.menu_index].enabled:
                    self.menu_index = prev_index
                self.update_menu_display()
            else:
                self.move_cursor_up()
        elif key == 'down':
            if self.active_menu is not None:
                menu = self.menus[self.active_menu]
                prev_index = self.menu_index
                while self.menu_index < len(menu.items) - 1:
                    self.menu_index += 1
                    if menu.items[self.menu_index].enabled:
                        break
                if not menu.items[self.menu_index].enabled:
                    self.menu_index = prev_index
                self.update_menu_display()
            else:
                self.move_cursor_down()
        elif key == 'left':
            if self.active_menu is not None:
                if self.active_menu > 0:
                    self.active_menu -= 1
                    self.menu_index = 0
                    self.update_menu_display()
            else:
                self.move_cursor_left()
        elif key == 'right':
            if self.active_menu is not None:
                if self.active_menu < len(self.menus) - 1:
                    self.active_menu += 1
                    self.menu_index = 0
                    self.update_menu_display()
            else:
                self.move_cursor_right()
        elif key == 'enter':
            if self.active_menu is not None:
                menu = self.menus[self.active_menu]
                if 0 <= self.menu_index < len(menu.items):
                    item = menu.items[self.menu_index]
                    if item.enabled and item.action:
                        self.hide_all_menus()
                        item.action()
            else:
                self.toggle_selection()
        elif key == 'esc' or key == 'escape' or key == '\x1b':
            if self.active_menu is not None:
                self.hide_all_menus()
            else:
                self.log_debug("ESC key detected, clearing selection")
                self.clear_selection()
        elif key == 'a' or key == 'A':
            self.prompt_for_entity()
        elif key == 'd' or key == 'D':
            self.delete_selected_entity()
        elif key == 'r' or key == 'R':
            self.prompt_for_rename()
        elif key == 'u' or key == 'U':
            self.prompt_for_unit()
        elif key == 'q' or key == 'Q':
            raise urwid.ExitMainLoop()

        if key in ('n', 'N'):
            if self.selected_entity_index is not None:
                entity = self.entities[self.selected_entity_index]
                if entity.startswith('switch.') or entity.startswith('light.'):
                    self.menu_on_switch()
                    self._refresh_entity_state_cache()
                    self.update_ui()
                    return
        if key in ('f', 'F'):
            if self.selected_entity_index is not None:
                entity = self.entities[self.selected_entity_index]
                if entity.startswith('switch.') or entity.startswith('light.'):
                    self.menu_off_switch()
                    self._refresh_entity_state_cache()
                    self.update_ui()
                    return

        if self.editing == 'error_modal':
            if key == 'enter' or key == 'esc' or key == 'escape':
                self.editing = False
                self.main_widget.body = self.build_grid()
            return

        self._update_context_help()

    def get_entity_index_from_position(self, x, y):
        return y * self.grid_width + x

    def get_position_from_entity_index(self, index):
        if index >= len(self.entities):
            return None, None
        return index % self.grid_width, index // self.grid_width

    def move_cursor_up(self):
        if self.selected_entity_index is not None:
            if self.selected_entity_index >= self.grid_width:
                old_index = self.selected_entity_index
                new_index = old_index - self.grid_width
                self.move_entity(old_index, new_index)
        else:
            if self.cursor_y > 0:
                self.cursor_y -= 1
                self.cursor_visible = True
                self.update_ui()
            elif not self.cursor_visible:
                self.cursor_visible = True
                self.update_ui()

    def move_cursor_down(self):
        if self.selected_entity_index is not None:
            new_index = self.selected_entity_index + self.grid_width
            if new_index < len(self.entities):
                old_index = self.selected_entity_index
                self.move_entity(old_index, new_index)
        else:
            if not self.cursor_visible:
                self.cursor_visible = True
                self.update_ui()
            else:
                max_y = (len(self.entities) - 1) // self.grid_width
                if self.cursor_y < max_y:
                    self.cursor_y += 1
                    self.update_ui()

    def move_cursor_left(self):
        if self.selected_entity_index is not None:
            if self.selected_entity_index % self.grid_width > 0:
                old_index = self.selected_entity_index
                new_index = old_index - 1
                self.move_entity(old_index, new_index)
        else:
            if not self.cursor_visible:
                self.cursor_visible = True
                self.update_ui()
            elif self.cursor_x > 0:
                self.cursor_x -= 1
                self.update_ui()

    def move_cursor_right(self):
        if self.selected_entity_index is not None:
            if (self.selected_entity_index % self.grid_width) < (self.grid_width - 1):
                old_index = self.selected_entity_index
                new_index = old_index + 1
                if new_index < len(self.entities):
                    self.move_entity(old_index, new_index)
        else:
            if not self.cursor_visible:
                self.cursor_visible = True
                self.update_ui()
            elif self.cursor_x < self.grid_width - 1:
                self.cursor_x += 1
                self.update_ui()

    def move_entity(self, old_index, new_index): # wheeee move entities around all willy-nilly
        if 0 <= old_index < len(self.entities) and 0 <= new_index < len(self.entities):
            self.log_debug(f"Moving entity from index {old_index} to {new_index}")
            self.log_debug(f"Before move: {self.entities}")
            entity = self.entities.pop(old_index)
            self.entities.insert(new_index, entity)
            self.selected_entity_index = new_index
            self.log_debug(f"After move: {self.entities}")
            new_x, new_y = self.get_position_from_entity_index(new_index)
            if new_x is not None and new_y is not None:
                self.cursor_x = new_x
                self.cursor_y = new_y
            self.update_ui()
            self.save_entity_order()

    def toggle_selection(self):
        current_index = self.get_entity_index_from_position(self.cursor_x, self.cursor_y)
        
        if current_index >= len(self.entities):
            return
            
        if self.selected_entity_index == current_index:
            self.selected_entity_index = None
            self.log_debug(f"Deselected entity at position ({self.cursor_x}, {self.cursor_y})")
        else:
            self.selected_entity_index = current_index
            self.log_debug(f"Selected entity at position ({self.cursor_x}, {self.cursor_y}): {self.entities[current_index]}")
        
        self.update_ui()

    def clear_selection(self):
        if self.selected_entity_index is not None:
            self.selected_entity_index = None
            self.log_debug("Cleared selection")
            self.update_ui()
        # Hide cursor when ESC is pressed for CLEANNESS
        if self.cursor_visible:
            self.cursor_visible = False
            self.log_debug("Hid cursor")
            self.update_ui()

    def prompt_for_entity(self):
        self.log_debug("Prompting for entity to add")
        self.editing = True
        self.edit = urwid.Edit("Entity ID: ")
        self.main_widget.body = urwid.Filler(self.edit)

    def delete_selected_entity(self):
        if self.selected_entity_index is not None and 0 <= self.selected_entity_index < len(self.entities):
            entity_to_delete = self.entities[self.selected_entity_index]
            self.log_debug(f"Deleting entity: {entity_to_delete}")
            self.entities.pop(self.selected_entity_index)
            self.config.data["entities"] = self.entities
            self.config.save_config()
            self.selected_entity_index = None
            self.update_ui()

    def save_entity_order(self):
        self.config.data["entities"] = self.entities
        self.config.save_config()
        self.log_debug("Entity order saved to config.")

    def add_entity(self, entity_id):
        self.entities.append(entity_id)
        self.config.data["entities"] = self.entities
        self.config.save_config()
        self.log_debug(f"Saved {entity_id} to config.")
        if self.loop:
            self.update_ui()

    def update_ui(self):
        self.log_debug("Updating UI.")
        new_grid = self.build_grid()
        self.main_widget.body = new_grid

    def build_grid(self):
        self._footer_entity_count = len(self.entities)
        self._update_context_help()
        self._update_footer_right()
        self.log_debug(f"Building grid with {self._footer_entity_count} entities")
        if not self.ha:
            self.log_debug("HA object not available in build_grid.")
            return urwid.Filler(urwid.Text("No HA connection"))
        
        rows = (len(self.entities) + self.grid_width - 1) // self.grid_width
        
        grid_rows = []
        for row in range(rows):
            row_widgets = []
            for col in range(self.grid_width):
                entity_index = row * self.grid_width + col
                if entity_index < len(self.entities):
                    entity = self.entities[entity_index]
                    try:
                        state = self._get_entity_state(entity)
                        if state is None:
                            raise Exception("No state found")

                        pending = self._pending_entity_states.get(entity)
                        if pending is not None:
                            state = dict(state)  # copy
                            state['state'] = pending
                        
                        # Determine display name: custom name > friendly name > entity ID
                        display_name = self.display_names.get(entity)
                        if not display_name and 'friendly_name' in state['attributes']:
                            display_name = state['attributes']['friendly_name']
                        if not display_name:
                            display_name = entity
                        
                        # Add unit if available
                        unit = self.units.get(entity, "")
                        value_with_unit = state['state']
                        if unit:
                            value_with_unit = f"{state['state']} {unit}"

                        # Fancy rendering of switches
                        if entity.startswith('switch.') or entity.startswith('light.'):
                            # Green glow for switches that are on
                            is_on = str(state['state']).lower() == 'on'
                            switch_label = 'ON' if is_on else 'OFF'
                            display = f"{display_name}: [{switch_label}]"
                            entity_text = urwid.Text(display)
                            entity_box = urwid.LineBox(entity_text)
                            if entity_index == self.selected_entity_index:
                                if is_on:
                                    entity_box = urwid.AttrMap(entity_box, 'switch_on_selected')
                                else:
                                    entity_box = urwid.AttrMap(entity_box, 'selected')
                            elif self.cursor_visible and row == self.cursor_y and col == self.cursor_x:
                                entity_box = urwid.AttrMap(entity_box, 'cursor')
                            elif is_on:
                                entity_box = urwid.AttrMap(entity_box, 'switch_on')
                        else:
                            entity_text = urwid.Text(f"{display_name}: {value_with_unit}")
                            entity_box = urwid.LineBox(entity_text)
                            if entity_index == self.selected_entity_index:
                                entity_box = urwid.AttrMap(entity_box, 'selected')
                            elif self.cursor_visible and row == self.cursor_y and col == self.cursor_x:
                                entity_box = urwid.AttrMap(entity_box, 'cursor')
                        
                        row_widgets.append(entity_box)
                    except Exception as e:
                        display_name = self.display_names.get(entity, entity)
                        error_text = urwid.Text(f"Error: {display_name}")
                        error_box = urwid.LineBox(error_text)
                        
                        if entity_index == self.selected_entity_index:
                            error_box = urwid.AttrMap(error_box, 'selected')
                        elif self.cursor_visible and row == self.cursor_y and col == self.cursor_x:
                            error_box = urwid.AttrMap(error_box, 'cursor')
                        
                        row_widgets.append(error_box)
                else:
                    row_widgets.append(urwid.Text("")) # catch empty cells
            
            grid_rows.append(urwid.Columns(row_widgets, dividechars=2))
        
        # Draw th grid
        grid = urwid.Pile(grid_rows)
        return urwid.Filler(grid, 'top')

    def prompt_for_unit(self):
        if self.selected_entity_index is not None:
            entity = self.entities[self.selected_entity_index]
            current_unit = self.units.get(entity, "")
            prompt = (f"Set unit for entity: {entity}\n"
                      f"Current unit: {current_unit or '(none)'}\n"
                      f"Enter new unit and press [Enter] to confirm:")
            self.log_debug(f"Prompting unit for {entity}")
            self.editing = 'unit'
            self.edit = urwid.Edit(prompt + "\n> ", current_unit)
            self.main_widget.body = urwid.Filler(self.edit)
        else:
            self.show_status_error("Select an entity first!")
            self.log_debug("No entity selected to set unit.")

    def save_unit(self, entity, unit):
        if unit.strip():
            self.units[entity] = unit.strip()
        else:
            # don't display custom unit if empty
            self.units.pop(entity, None)
        
        self.config.data["units"] = self.units
        self.config.save_config()
        self.log_debug(f"Saved unit for {entity}: {unit}")
        self.update_ui()

    def menu_on_switch(self):
        if self.selected_entity_index is not None:
            entity = self.entities[self.selected_entity_index]
            self.log_debug(f"Turning entity on: {entity}")
            try:
                result = self.ha.turn_on_entity_ws(entity)
                self.log_debug(f"Turn on result: {result}")
                self._pending_entity_states[entity] = 'on'
                self.update_ui()
            except Exception as e:
                self.log_debug(f"Error turning switch on: {e}")
            self.update_ui()
        else:
            self.log_debug("No switch selected for this action (ON)!")

    def menu_off_switch(self):
        if self.selected_entity_index is not None:
            entity = self.entities[self.selected_entity_index]
            self.log_debug(f"Turning switch off: {entity}")
            try:
                result = self.ha.turn_off_entity_ws(entity)
                self.log_debug(f"Turn off result: {result}")
                self._pending_entity_states[entity] = 'off'
                self.update_ui()
            except Exception as e:
                self.log_debug(f"Error turning switch off: {e}")
            self.update_ui()
        else:
            self.log_debug("No switch selected for this action (OFF)!")

    def _update_footer_right(self, loop=None, user_data=None):
        if self.is_menu_open() or self.is_modal_open():
            if self.loop:
                self.loop.set_alarm_in(1, self._update_footer_right)
            return
        # The statusbar looks cool and all with a clock, but it takes up space.
        # If you want a clock, uncomment 827/828 and comment 829.
        #now = datetime.now().strftime('%H:%M')
        #right_text = f"{self._footer_entity_count} entities - {now}"
        right_text = f""
        self.footer_right.set_text(right_text)
        if self.loop:
            self.loop.set_alarm_in(1, self._update_footer_right)

    def _update_context_help(self):
        if hasattr(self, '_error_active') and self._error_active:
            return  # Don't be dumb and overwrite error messages
        help_text = ""
        idx = self.get_entity_index_from_position(self.cursor_x, self.cursor_y)
        if self.cursor_visible and 0 <= idx < len(self.entities):
            entity_id = self.entities[idx]
            is_switch_or_light = entity_id.startswith('switch.') or entity_id.startswith('light.')
            if self.selected_entity_index is not None:
                help_text = "[Arrow keys] - move | [Enter] drop"
            elif is_switch_or_light:
                help_text = "[T] or [space] - toggle | [Enter] - grab/select to modify | [Esc] - clear highlight"
            else:
                help_text = "[Enter] - grab/select to modify | [Esc] - clear highlight"
        self.footer_help.set_text(help_text)
        if self.loop:
            self.loop.draw_screen()

    def _refresh_entity_state_cache(self): # caching states from HA for more fastness and less slowness
        try:
            all_states = self.ha._run_async(self.ha._ws_request('get_states'))
            self._entity_state_cache = {s['entity_id']: s for s in all_states}
            self._entity_state_cache_time = time.time()
            self.log_debug(f"Refreshed entity state cache with {len(self._entity_state_cache)} entities.")
        except Exception as e:
            self.log_debug(f"Error refreshing entity state cache: {e}")
            self._entity_state_cache = {}
            self._entity_state_cache_time = time.time()

    def _get_entity_state(self, entity_id):
        if time.time() - self._entity_state_cache_time > self._entity_state_cache_ttl:
            self._refresh_entity_state_cache()
        return self._entity_state_cache.get(entity_id)

    def _periodic_refresh(self, loop=None, user_data=None): #updating UI every 2 seconds no matter what BAAAAAAAH FUCK YOUR NETWORK SEND IT
        if not self.is_menu_open() and not self.is_modal_open():
            self._refresh_entity_state_cache()
            self.update_ui()
        if self.loop:
            self.loop.set_alarm_in(2, self._periodic_refresh)
