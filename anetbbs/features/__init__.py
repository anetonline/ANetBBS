# anetbbs/features/__init__.py
# Intentionally empty — eager-importing from .chat causes a circular import
# (chat -> base_chat -> core.protocols -> core/__init__ -> core/session ->
# features/chat, which is mid-load). Submodules import what they need
# explicitly: `from anetbbs.features.chat import ChatManager`.
