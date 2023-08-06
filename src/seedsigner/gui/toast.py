from dataclasses import dataclass
from seedsigner.gui.components import BaseComponent, GUIConstants, Icon, TextArea
from seedsigner.models.threads import BaseThread



@dataclass
class ToastOverlay(BaseComponent):
    icon_name: str = None
    color: str = GUIConstants.NOTIFICATION_COLOR
    label_text: str = None
    height: int = GUIConstants.ICON_TOAST_FONT_SIZE + 2*GUIConstants.EDGE_PADDING
    font_size: int = 19
    outline_thickness: int = 2  # pixels

    def __post_init__(self):
        super().__post_init__()

        self.icon = Icon(
            image_draw=self.image_draw,
            canvas=self.canvas,
            screen_x=self.outline_thickness + 2*GUIConstants.EDGE_PADDING,  # Push the icon further from the left edge than strictly necessary
            icon_name=self.icon_name,
            icon_size=GUIConstants.ICON_TOAST_FONT_SIZE,
            icon_color=self.color
        )
        self.icon.screen_y = self.canvas_height - self.height + int((self.height - self.icon.height)/2) - 1  # -1 fudge factor
        
        self.label = TextArea(
            image_draw=self.image_draw,
            canvas=self.canvas,
            text=self.label_text,
            font_size=self.font_size,
            font_color=self.color,
            edge_padding=0,
            is_text_centered=False,
            auto_line_break=True,
            width=self.canvas_width - self.icon.screen_x - self.icon.width - GUIConstants.COMPONENT_PADDING - self.outline_thickness,
            screen_x=self.icon.screen_x + self.icon.width + GUIConstants.COMPONENT_PADDING,
            allow_text_overflow=False
        )
        self.label.screen_y = self.canvas_height - self.height + int((self.height - self.label.height)/2)


    def render(self):
        self.image_draw.rounded_rectangle(
            (0, self.canvas_height - self.height, self.canvas_width, self.canvas_height),
            fill=GUIConstants.BACKGROUND_COLOR,
            radius=8,
            outline=self.color,
            width=self.outline_thickness,
        )

        self.icon.render()
        self.label.render()

        self.renderer.show_image()




class BaseToastOverlayManagerThread(BaseThread):
    """
    The toast notification popup consists of a gui component (`ToastOverlay`) and this
    manager thread that the Controller will use to coordinate handing off resources
    between competing toasts, the screensaver, and the current underlying Screen.

    Controller should set BaseThread.keep_running = False to terminate the toast when it
    needs to be removed or replaced.

    Controller should set toggle_renderer_lock = True to make the toast temporarily
    release the Renderer.lock so another process (e.g. screensaver) can use it. The toast
    thread will immediately try to reacquire the lock, but will have to block and wait
    until it's available again. Note that this thread will be unresponsive while it
    waits to reacquire the lock!

    Note: any process can call lock.release() but it simplifies the logic to try to keep
    each process aware of whether it is currently holding the lock or not (i.e. it's 
    better for the "owner" thread to release the lock itself).
    """
    def __init__(self,
                 activation_delay: int = 0,  # seconds before toast is displayed
                 duration: int = 3,          # seconds toast is displayed
                 ):
        from seedsigner.controller import Controller
        from seedsigner.gui.renderer import Renderer
        from seedsigner.hardware.buttons import HardwareButtons
        super().__init__()
        self.activation_delay: int = activation_delay
        self.duration: int = duration
        self._toggle_renderer_lock: bool = False

        self.renderer = Renderer.get_instance()
        self.controller = Controller.get_instance()
        self.hw_inputs = HardwareButtons.get_instance()

        # Special case when screensaver is running
        self.hw_inputs.override_ind = True

        self.toast = self.instantiate_toast()
    

    def instantiate_toast(self) -> ToastOverlay:
        raise Exception("Must be implemented by subclass")


    def should_keep_running(self) -> bool:
        """ Placeholder for custom exit conditions """
        return True


    def toggle_renderer_lock(self):
        self._toggle_renderer_lock = True


    def run(self):
        try:
            print(f"{self.__class__.__name__}: started")
            start = time.time()
            has_rendered = False
            self.previous_screen_state = None
            if self.activation_delay > 0:
                time.sleep(self.activation_delay)

            # Hold onto the Renderer lock so we're guaranteed to restore the original
            # screen before any other listener can get a screen write in.
            print(f"{self.__class__.__name__}: Acquiring lock")
            self.renderer.lock.acquire()
            print(f"{self.__class__.__name__}: Lock acquired")
            while self.keep_running and self.should_keep_running():
                if self.hw_inputs.has_any_input():
                    # User has pressed a button, hide the toast
                    print(f"{self.__class__.__name__}: Exiting due to user input")
                    break

                print(time.time(), self._toggle_renderer_lock)

                if self._toggle_renderer_lock:
                    # Controller has notified us that another process needs the lock
                    print(f"{self.__class__.__name__}: Releasing lock")
                    self._toggle_renderer_lock = False
                    self.renderer.lock.release()

                    # pause to avoid race conditions reacquiring the lock
                    while not self.renderer.lock.locked():
                        # Wait for a different process to grab the lock
                        time.sleep(0.1)

                    # Block while waiting to reaquire the lock
                    print(f"{self.__class__.__name__}: Blocking to re-acquire lock")
                    self.renderer.lock.acquire()
                    print(f"{self.__class__.__name__}: Lock re-acquired")

                if not has_rendered:
                    self.previous_screen_state = self.renderer.canvas.copy()
                    print(f"{self.__class__.__name__}: Showing toast")
                    self.toast.render()
                    has_rendered = True
                
                if time.time() - start > self.activation_delay + self.duration and has_rendered:
                    print(f"{self.__class__.__name__}: Hiding toast")
                    break

                # Free up cpu resources for main thread
                time.sleep(0.1)

        finally:
            print(f"{self.__class__.__name__}: exiting")
            if has_rendered and self.renderer.lock.locked():
                # As far as we know, we currently hold the Renderer.lock
                self.renderer.show_image(self.previous_screen_state)
            
            # We're done, release the lock
            self.renderer.lock.release()



class RemoveSDCardToastManagerThread(BaseToastOverlayManagerThread):
    def __init__(self):
        super().__init__(
            activation_delay=3,  # seconds
            duration=1e6,        # seconds ("forever")
        )


    def instantiate_toast(self) -> ToastOverlay:
        return ToastOverlay(
            icon_name=FontAwesomeIconConstants.SDCARD,
            label_text="Security tip:\nRemove SD card",
            font_size=GUIConstants.BODY_FONT_SIZE,
            height=GUIConstants.BODY_FONT_SIZE * 2 + GUIConstants.BODY_LINE_SPACING + GUIConstants.EDGE_PADDING,
        )
        

    def should_keep_running(self) -> bool:
        """ Custom exit condition: keep running until the SD card is removed """
        from seedsigner.hardware.microsd import MicroSD
        return MicroSD.is_inserted()



class SDCardStateChangeToastManagerThread(BaseToastOverlayManagerThread):
    def __init__(self, action: str, *args, **kwargs):
        from seedsigner.hardware.microsd import MicroSD
        if action not in [MicroSD.ACTION__INSERTED, MicroSD.ACTION__REMOVED]:
            raise Exception(f"Invalid MicroSD action: {action}")
        self.message = "SD card removed" if action == MicroSD.ACTION__REMOVED else "SD card inserted"

        super().__init__(*args, **kwargs)


    def instantiate_toast(self) -> ToastOverlay:
        return ToastOverlay(
            icon_name=FontAwesomeIconConstants.SDCARD,
            label_text=self.message,
        )


