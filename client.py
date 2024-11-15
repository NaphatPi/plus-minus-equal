import asyncio
import json

import urwid
import websockets
import websockets.connection


class ChatUI:
    def __init__(self, websocket_client=None, loop=None):

        self.websocket_client = websocket_client
        self.loop = loop
        # Chat message history
        self.messages = urwid.SimpleListWalker([])

        # Main chat box containing all messages
        self.chatbox = urwid.ListBox(self.messages)

        # Text input box for typing messages
        self.input_edit = urwid.Edit("> ")

        # Container for input box
        self.input_box = urwid.AttrMap(self.input_edit, "input")

        # Top sidebar - Leaderboard (ListBox with sample data)
        self.leaderboard_items = []
        self.leaderboard_listbox = urwid.ListBox(
            urwid.SimpleListWalker(self.leaderboard_items)
        )
        self.sidebar_top = urwid.LineBox(self.leaderboard_listbox, title="Leaderboard")

        self.tip_items = [
            urwid.Text("/play - to play a game"),
            urwid.Text("/reset - to reset score"),
        ]
        self.tip_listbox = urwid.ListBox(urwid.SimpleListWalker(self.tip_items))
        self.sidebar_mid = urwid.LineBox(self.tip_listbox, title="Command - Tips")

        # Bottom sidebar - Exit Button
        self.exit_button = urwid.Button("Exit", on_press=self.exit_program)
        self.exit_button = urwid.AttrMap(self.exit_button, None, focus_map="reversed")
        self.sidebar_bottom = urwid.LineBox(
            urwid.Padding(self.exit_button, align="center"), title="Options"
        )

        # Stack the two sidebars in a Pile layout, each taking 50% height
        self.sidebars = urwid.Pile(
            [
                ("weight", 2, self.sidebar_top),  # 50% height for leaderboard
                ("weight", 1, self.sidebar_mid),  # 25% height for exit button
                ("weight", 1, self.sidebar_bottom),  # 25% height for exit button
            ]
        )

        # Main chat area with chatbox and input box at the bottom
        self.chat_and_sidebar = urwid.Columns(
            [
                ("weight", 4, urwid.LineBox(self.chatbox)),  # 80% width
                ("weight", 1, self.sidebars),  # 20% width
            ]
        )

        self.main_content = urwid.Frame(
            body=self.chat_and_sidebar, footer=self.input_box
        )

        # Header at the top of the screen
        self.header = urwid.Text("Chat Interface", align="center")

        # Full layout with header, main content, and sidebars
        self.layout = urwid.Frame(header=self.header, body=self.main_content)

        self.layout.set_focus_path(["body", "footer"])

    def handle_input(self, key):
        # Handle input box enter key
        if key == "enter":
            # Get the text in the input box
            message = self.input_edit.get_edit_text()

            # If message is not empty, add to the chat box
            if message.strip():
                self.send_message(message)
                self.input_edit.set_edit_text("")  # Clear input box

    def update(self, message: str):
        """Update the UI based on the message"""
        try:
            response = json.loads(message)
        except:
            print("Error parsing json")
            return

        if response["target"] == "message":
            self.add_message(response["payload"])

        elif response["target"] == "leaderboard":
            self.update_leaderboard(response["payload"])

        self.loop.draw_screen()

    def update_leaderboard(self, boards: list[tuple]):
        """Update leaderboard"""
        boards = sorted(boards, key=lambda x: x[-1], reverse=True)
        boards = [
            urwid.Text(f"{idx}. {items[0]} - {items[1]}")
            for idx, items in enumerate(boards, 1)
        ]
        self.leaderboard_listbox.body = urwid.SimpleListWalker(boards)

    def add_message(self, message):
        # Add message to chatbox
        message_widget = urwid.Text(message)
        self.messages.append(urwid.AttrMap(message_widget, None))
        self.chatbox.set_focus(len(self.messages) - 1)  # Scroll to latest message

    def send_message(self, message):
        # Call the send function of WebSocketClient to send message over WebSocket
        asyncio.ensure_future(self.websocket_client.send_message(message))

    def exit_program(self, button):
        asyncio.get_event_loop().stop()


class WebSocketClient:
    def __init__(self, uri, ui, name):
        self.uri = uri
        self.ui = ui
        self.name = name
        self.websocket = None  # Will store the WebSocket connection

    async def connect(self):
        # WebSocket connection handler using context manager
        async with websockets.connect(self.uri) as websocket:
            self.websocket = websocket  # Store WebSocket connection
            # Start the task to listen for incoming messages
            await self.register()
            await self.listen()

    async def close(self):
        await self.websocket.close()

    async def register(self):
        await self.websocket.send(json.dumps({"action": "register", "name": self.name}))

        response = await self.websocket.recv()
        response = json.loads(response)

        if response["target"] == "error":
            raise ValueError(f"Failed to register: {response['payload']}")

    async def listen(self):
        # This method continuously listens for incoming messages
        while True:
            try:
                message = await self.websocket.recv()
                self.ui.update(message)
            except websockets.ConnectionClosedError:
                print("Connection closed by client.")
                break
            except Exception as e:
                print(f"Error: {e}")
                break

    async def send_message(self, message):
        # Function to send messages to the WebSocket server
        if self.websocket:
            try:
                await self.websocket.send(
                    json.dumps({"action": "message", "payload": message})
                )
            except Exception as e:
                print(f"Failed to send message: {e}")


def start():
    # WebSocket URI
    # uri = input("Server URI: ")
    name = input("Your name: ")

    websocket_uri = (
        "ws://localhost:8765"  # f"ws://{uri}" if "localhost" in uri else f"wss://{uri}"
    )

    # Initialize UI and pass the WebSocketClient instance
    ui = ChatUI()  # Temporarily passing None as websocket_client

    # Initialize WebSocket client and pass the UI instance
    websocket_client = WebSocketClient(websocket_uri, ui, name)
    ui.websocket_client = websocket_client  # Assign the WebSocketClient instance to UI

    # Create asyncio event loop and run WebSocket client in background
    loop = asyncio.get_event_loop()
    asyncio.ensure_future(websocket_client.connect())  # Start WebSocket listening

    # Use urwid's AsyncioEventLoop to integrate with asyncio
    urwid_loop = urwid.AsyncioEventLoop(loop=loop)

    # Create urwid main loop and pass the UI layout
    main_loop = urwid.MainLoop(
        ui.layout, unhandled_input=ui.handle_input, event_loop=urwid_loop
    )

    ui.loop = main_loop

    # Run the main event loop (blocking)
    main_loop.run()


if __name__ == "__main__":
    start()
