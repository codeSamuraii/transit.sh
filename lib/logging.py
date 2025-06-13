import logging


class ColoredFormatter(logging.Formatter):
    COLORS = {
        'BOLD': '\033[1m',      # Bold
        'ITALIC': '\033[3m',    # Italic
        'DEBUG': '\033[2m',     # Dim grey
        'INFO': '\033[32m',     # Green (darker shade like Uvicorn's INFO)
        'INFO_SENDER': '\033[36m',    # Cyan for sender
        'INFO_RECEIVER': '\033[94m',  # Light blue for receiver
        'WARNING': '\033[33m',  # Yellow
        'ERROR': '\033[31m',    # Red
        'CRITICAL': '\033[35m', # Magenta
    }
    RESET = '\033[0m'

    def format(self, record: logging.LogRecord) -> str:
        # Save the original values
        original_msg = record.msg
        original_levelname = record.levelname
        original_name = record.name

        result = super().format(record)

        # Level
        log_color = self.COLORS.get(original_levelname, self.RESET)
        levelname_colored = f"{log_color}{original_levelname:<9}{self.RESET}"

        # Name
        if 'uvicorn' in original_name:
            name_color = self.COLORS['DEBUG'] + self.COLORS['ITALIC']  # Dim grey for Uvicorn
            name_padded = f"{name_color}{'uvicorn':<17}{self.RESET}"
        elif original_name == 'websockets' or original_name == 'http':
            name_color = self.COLORS['DEBUG'] + self.COLORS['BOLD']  # Dim grey for Uvicorn
            name_padded = f"{name_color}{original_name:<15}{self.RESET}"
        else:
            name_padded = f"{original_name:<15}"

        # Message
        message = record.message  # This is the formatted message after getMessage()
        if isinstance(message, str):
            if message.startswith('△'):
                message = f"{self.COLORS['INFO_SENDER']}{self.COLORS['BOLD']}{message[:1]}{self.RESET}{message[1:]}"
            elif message.startswith('▼'):
                message = f"{self.COLORS['INFO_RECEIVER']}{self.COLORS['BOLD']}{message[:1]}{self.RESET}{message[1:]}"

        # Replace parts in the result with our colored versions
        # This approach preserves any non-standard formatting that might be in the original formatter
        parts = result.split(' ', 2)
        if len(parts) >= 3:
            return f"{levelname_colored} {name_padded} {message}"

        return result


def get_logger(logger_name: str) -> logging.Logger:
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    formatter = ColoredFormatter(
        fmt='{levelname} {name} {message}',
        datefmt='%H:%M:%S',
        style='{'
    )
    console_handler.setFormatter(formatter)

    # Create logger
    logger = logging.getLogger(logger_name)
    logger.handlers = []  # Clear existing handlers to prevent duplicates
    logger.setLevel(logging.DEBUG) # Set logger level
    logger.addHandler(console_handler)
    logger.propagate = False  # Prevent propagation to the root logger

    return logger


def setup_logging():
    """Configure all loggers to use our custom ColoredFormatter."""
    # Create the formatter
    formatter = ColoredFormatter(
        fmt='{levelname} {name} {message}',
        datefmt='%H:%M:%S',
        style='{'
    )

    # Configure the root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # Remove any existing handlers
    if root_logger.handlers:
        for handler in root_logger.handlers:
            root_logger.removeHandler(handler)

    # Add console handler with our formatter
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Configure Uvicorn loggers
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(logger_name)
        logger.handlers = []  # Remove default handlers
        logger.propagate = False  # Don't propagate to root logger
        logger.addHandler(console_handler)

    # Set specific log levels
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
