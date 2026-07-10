"""Anet's canned personality wrapper: fixed intro/outro/fallback strings
filled into a template. No model, no generation, no external calls -- see
DISCLOSURE below, shown at every entry point (terminal and web) so this is
never mistaken for a live AI chatbot.
"""
import random

DISCLOSURE = (
    "Heads up: Anet isn't a chatbot. She's a smart search tool that "
    "looks through the BBS wiki's help pages for words matching your "
    "question, wrapped in a friendly retro voice. No AI is 'thinking' "
    "about what you type -- it's the same kind of keyword search any "
    "search box does, just dressed up in BBS style."
)

INTROS = [
    "Hi, I'm Anet! Ask me where to find something and I'll search the wiki for you.",
    "Anet here -- tell me what you're looking for and I'll point you at the right menu.",
]

FOUND_LEAD_INS = [
    "Here's what I found:",
    "These wiki pages look like a match:",
]

NOT_FOUND = (
    "Hmm, nothing matched that in the wiki. Try different words -- "
    "or browse the full wiki index to see everything covered."
)

OUTRO = "Ask another question, or leave blank to go back."


def intro():
    return random.choice(INTROS)


def found_lead_in():
    return random.choice(FOUND_LEAD_INS)


def not_found():
    return NOT_FOUND
