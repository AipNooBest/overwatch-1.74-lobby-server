"""Chat channels and whispers, and the commands a player types in any chat while in a match:
.hero <name> plays another hero, .leave goes back to the menu, .pick closes the hero select
screen, and the debug set - .hud, .pres,
.banner - turns the live server's knobs without a restart (see docs/HERO-WEAPONS.md)."""

from ow174.accounts.registry import Account
from ow174.game.content import find_hero
from ow174.jam.groups import CHAT_IN, CHAT_OUT, FRIENDS, FRIENDS_OUT
from ow174.lobby.router import Router
from ow174.lobby.session import Session

routes = Router()

BOT_REPLY = "{name}, I hear you: {text}"


@routes.on(CHAT_OUT, 21700)
def chat(session: Session, value: dict) -> None:
    social = session.server.social
    channel = value.get("+0x78") or {}
    text = value.get("+0x90") or ""
    if text.startswith(".") and _match_command(session, channel, text):
        return
    message = social.chat_message(channel, session.account, text, value.get("+0xB8", 0))
    members = social.channel_members(channel)
    _deliver(session.server, members, message)
    session.log(f"[chat #{channel.get('+0x10')}] {session.account.name}: {text}")
    bot = social.accounts.bot
    if bot in members and session.profile.bot_chat:
        reply_text = BOT_REPLY.format(name=session.account.name, text=text)
        _deliver(session.server, members, social.chat_message(channel, bot, reply_text))


DEBUG_HELP = (
    ".hero <name>, .leave, .pick (close the hero select), .hudroot (ring the HUD layer "
    "listener), .mount <on|off> (re-create the "
    "HUD widgets), .mode <on|off> (the game mode's statescript), .hud (the group banner), "
    ".pres <on|off> (weapon-script presenters), .banner <on|off> (matches ride the banner), "
    ".hudvar <var> <value> (rewrite a hero-HUD variable, bare = list, clear = reset, re = "
    "destroy-create the bodies so rewrites apply), .hudslot <inst> <slot> <on|off> (turn one "
    "weapon-script state on or off, bare = list, clear = reset), .help"
)


def _match_command(session: Session, channel: dict, text: str) -> bool:
    """Run a match or debug command. False when it is not one, so the text goes out as chat."""
    game = session.server.game
    command, _, rest = text[1:].partition(" ")
    command = command.lower()
    if command not in ("hero", "leave", "pick", "help", "hud", "hudroot", "mount", "pres",
                       "banner", "mode", "hudvar", "hudslot"):
        return False
    if command == "help":
        reply = DEBUG_HELP
    elif command == "mount":
        if game is None:
            reply = "The game server is off."
        else:
            on = _switch(rest)
            game.ui_mounts(on)
            reply = f"HUD widgets {'re-created' if on else 'destroyed'}."
    elif command == "hudroot":
        if game is None:
            reply = "The game server is off."
        else:
            count = game.ui_hud_messages()
            reply = f"Layer listener rung on {count} match(es)."
    elif command == "mode":
        if game is None:
            reply = "The game server is off."
        else:
            on = _switch(rest)
            game.set_mode_graph(on)
            reply = f"Game mode statescript {'on' if on else 'off'}."
    elif command == "hero":
        hero = find_hero(rest.strip())
        if hero is None:
            reply = f"No hero called '{rest.strip()}'."
        elif game is None or not game.switch_hero(session.account.account_lo, hero):
            reply = "You are not in a match."
        else:
            reply = f"You play {hero.name} now."
    elif command == "leave":
        reply = (
            "Back to the menu." if game is not None and game.send_home(session.account.account_lo)
            else "You are not in a match."
        )
    elif command == "pick":
        reply = (
            "Hero select closed."
            if game is not None and game.close_hero_select(session.account.account_lo)
            else "You are not in a match."
        )
    elif command == "hud":
        session.server.matchmaker.banner_now(session)
        reply = "Banner sent: mode 5 for the accept window."
    elif command == "hudvar":
        reply = "The game server is off." if game is None else game.hud_var(session.account.account_lo, rest)
    elif command == "hudslot":
        reply = "The game server is off." if game is None else game.hud_slot(session.account.account_lo, rest)
    elif command == "pres":
        if game is None:
            reply = "The game server is off."
        else:
            on = _switch(rest)
            count = game.set_gun_presenters(on)
            reply = f"Weapon presenters {'on' if on else 'off'}, {count} match(es) re-sent."
    else:  # banner
        on = _switch(rest)
        session.server.matchmaker.banner_on = on
        reply = f"Matches {'ride' if on else 'skip'} the group banner."
    session.log(f"[chat] {text} -> {reply}")
    bot = session.server.social.accounts.bot
    session.send(CHAT_IN, 20400, session.server.social.chat_message(channel, bot, reply))
    return True


def _switch(rest: str) -> bool:
    """on|off|1|0 for the debug switches."""
    return rest.strip().lower() in ("on", "1", "true", "yes")


def _deliver(server, members: list[Account], message: dict) -> None:
    for account in members:
        recipient = server.session_of(account.account_lo)
        if recipient:
            recipient.send(CHAT_IN, 20400, message)


@routes.on(CHAT_OUT, 21701)
def channel_members(session: Session, value: dict) -> None:
    session.send(CHAT_IN, 20401, session.server.social.who(value.get("+0x78") or {}))


@routes.on(FRIENDS_OUT, 27004)
def whisper(session: Session, value: dict) -> None:
    """A Battle.net whisper. 27116 completes the request, and the target gets 27117 {sender, text}.

    The sender's client shows its own line itself, so it gets no copy.
    """
    text = value.get("+0xA0") or ""
    session.send(FRIENDS, 27116, {"+0x78": value.get("+0x78", 0), "+0x80": 0})
    target = _whisper_target(session, value)
    if target is None:
        session.log(f"[<<<] Whisper to unknown player {value}")
        return
    session.log(f"[whisper] {session.account.name} -> {target.name}: {text}")
    if target.virtual:
        if session.profile.bot_chat:
            reply = BOT_REPLY.format(name=session.account.name, text=text)
            session.send(FRIENDS, 27117, {"+0x78": target.account, "+0x88": reply})
        return
    recipient = session.server.session_of(target.account_lo)
    if recipient:
        recipient.send(FRIENDS, 27117, {"+0x78": session.account.account, "+0x88": text})


def _whisper_target(session: Session, value: dict) -> Account | None:
    """The first known player in the whisper who is not the sender. Either field may hold the sender."""
    accounts = session.server.social.accounts
    for key in ("+0x80", "+0x90"):
        player = value.get(key) or {}
        account = accounts.by_id(player.get("+0x0", 0))
        if account and account.account_lo != session.account.account_lo:
            return account
    return None
