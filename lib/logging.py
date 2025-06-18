import logging


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


def setup_logging():
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

    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
