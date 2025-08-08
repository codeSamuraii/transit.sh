import logging


# ---- FORMATTING ----

class ColoredFormatter(logging.Formatter):
    """Custom formatter with colored output and specific formatting for transfers."""

    COLORS = {
        'BOLD': '\033[1m',          # Bold
        'ITALIC': '\033[3m',        # Italic
        'DEBUG': '\033[2m',         # Dim grey
        'INFO': '\033[32m',         # Green
        'UP_ARROW': '\033[36m',     # Cyan for sender
        'DOWN_ARROW': '\033[94m',   # Light blue for receiver
        'WARNING': '\033[33m',      # Yellow
        'ERROR': '\033[31m',        # Red
        'CRITICAL': '\033[35m',     # Magenta
    }
    RESET = '\033[0m'

    def format(self, record: logging.LogRecord) -> str:
        # Save the original values
        original_levelname = record.levelname
        original_name = record.name

        result = super().format(record)

        # Level
        log_color = self.COLORS.get(original_levelname, self.RESET)
        levelname_colored = f"{log_color}{original_levelname:<9}{self.RESET}"

        # Name
        if 'uvicorn' in original_name:
            name_color = self.COLORS['DEBUG'] + self.COLORS['ITALIC']   # Italic grey for uvicorn
            name_padded = f"{name_color}{'uvicorn':<17}{self.RESET}"
        elif original_name == 'websockets' or original_name == 'http':
            name_color = self.COLORS['ITALIC']                          # Italic white for views
            name_padded = f"{name_color}{original_name:<15}{self.RESET}"
        else:
            name_padded = f"{original_name:<15}"

        # Message
        message = record.message
        if isinstance(message, str):
            if message.startswith('△'):
                message = f"{self.COLORS['UP_ARROW']}{self.COLORS['BOLD']}{message[:1]}{self.RESET}{message[1:]}"
            elif message.startswith('▼'):
                message = f"{self.COLORS['DOWN_ARROW']}{self.COLORS['BOLD']}{message[:1]}{self.RESET}{message[1:]}"

        # Format the exception if present
        exc_text = ""
        if record.exc_info:
            exc_text = f"\n{self.formatException(record.exc_info)}"

        # Replace parts in the result with our colored versions
        # This approach preserves any non-standard formatting that might be in the original formatter
        parts = result.split(' ', 2)
        if len(parts) >= 3:
            return f"{levelname_colored} {name_padded} {message}{exc_text}"

        return result + exc_text

class HealthCheckFilter(logging.Filter):
    def filter(self, record):
        return '"GET /health HTTP/1.1" 200' not in record.getMessage()


# ---- LOGGING SETUP ----

def setup_logging() -> logging.Logger:
    """Configure all loggers to use our custom ColoredFormatter."""
    formatter = ColoredFormatter(
        fmt='{levelname} {name} {message}',
        datefmt='%H:%M:%S',
        style='{'
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    if root_logger.handlers:
        for handler in root_logger.handlers:
            root_logger.removeHandler(handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(logger_name)
        logger.handlers = []
        logger.propagate = False
        logger.addHandler(console_handler)

    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").addFilter(HealthCheckFilter())

    return root_logger

def get_logger(logger_name: str) -> logging.Logger:
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    formatter = ColoredFormatter(
        fmt='{levelname} {name} {message}',
        datefmt='%H:%M:%S',
        style='{'
    )
    console_handler.setFormatter(formatter)

    logger = logging.getLogger(logger_name)
    logger.handlers = []
    logger.setLevel(logging.DEBUG)
    logger.addHandler(console_handler)
    logger.propagate = False

    return logger


# ---- PATCHING ----

class HasLogging(type):
    """Metaclass that automatically adds logging methods and a logger property."""

    def __new__(mcs, name, bases, namespace, **kwargs):
        name_from = kwargs.get('name_from', 'name')

        @property
        def logger(self):
            if not hasattr(self, '_logger'):
                class_name = self.__class__.__name__
                fallback_name = class_name + str(id(self))[-4:]
                logger_name = getattr(self, name_from, fallback_name)
                self._logger = get_logger(logger_name)
                if not hasattr(self, name_from):
                    self._logger.warning(
                        f"Object {class_name} does not have attribute '{name_from}', "
                        f"using default name: {logger_name}"
                    )
            return self._logger

        namespace['logger'] = logger

        def make_log_method(level):
            def log_method(self, msg, *args, **kwargs):
                getattr(self.logger, level)(msg, *args, **kwargs)
            return log_method

        for level in {'debug', 'info', 'warning', 'error', 'exception', 'critical'}:
            namespace[level] = make_log_method(level)

        return super().__new__(mcs, name, bases, namespace)
