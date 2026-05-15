from abc import ABC, abstractmethod
from ..core.protocols import SessionProtocol

class BaseChatSystem(ABC):
    def __init__(self, session: SessionProtocol):
        self.session = session

    @abstractmethod
    async def show_menu(self) -> None:
        """Show the chat system menu"""
        pass
