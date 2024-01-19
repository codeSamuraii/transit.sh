from textual import work
from textual.widget import Widget
from textual.reactive import reactive
from textual.app import App, ComposeResult
from textual.widgets import Label, Button
from pathlib import Path

from helpers import send_file_async


class FileInfo(Widget):
    """Selected file info."""

    filename = reactive("")
    filepath = reactive("")

    def render(self) -> str:
        if not self.filename:
            return f"Drop a file to be transfered"
        else:
            return f"File: {self.filename}\nSize: {self.filepath.stat().st_size / 1024:.1f} KiB"


class TransferApp(App):
    CSS_PATH = "vertical_layout.tcss"

    def compose(self) -> ComposeResult:
        yield Label("Transit.sh", id="title", classes="header")
        yield FileInfo(id="fileinfo", classes="box")
        yield Button("Send", disabled=True, id="sendbutton", classes="footer")
    
    def on_paste(self, event) -> None:
        file_path = Path(event.text.strip()).resolve()

        fi = self.query_one('#fileinfo')
        fi.filename = file_path.name
        fi.filepath = file_path

        sb = self.query_one('#sendbutton')
        sb.variant = 'success'
        sb.disabled = False
    
    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == 'sendbutton':
            self.run_worker(self.action_send_file())
    
    async def action_send_file(self) -> None:
        fi = self.query_one('#fileinfo')
        sb = self.query_one('#sendbutton')

        sb.variant = 'warning'
        sb.label = 'Sending...'

        response = await send_file_async(fi.filepath, 'test')

        if response == 200:
            sb.variant = 'success'
            sb.label = 'Sent!'
        else:
            sb.variant = 'error'
            sb.label = 'Error!'



if __name__ == "__main__":
    app = TransferApp()
    reply = app.run()
    print(reply)