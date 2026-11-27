"""Render the six case-note textures with the approved memo typography."""
from PIL import Image, ImageDraw, ImageFont

from build_memo1_test import COLUMNS, PATH
import graphics
from translation_source import extent


PAGES = (
    COLUMNS,
    (
        """Hazuki: With Mimura in his room after dinner.
Mayura: In her room. Upstairs during Manami and Mimura's argument.
Yone: Clearing up in the kitchen and dining room.
Manami: Cleaned the house. Argued with Mimura outside Hazuki's room, then went to the clock controls with Itsuki.""",
        """Mimura: With Endo in her room, then in Hazuki's room.
Endo: Mimura's room, then from the dining room to his room.
Sayuri: Dining room, then her room. Looked for Itsuki, then back to the dining room with Miyuki and me.
Tomyo: Dining room. Left for the toilet for about 15 minutes.""",
    ),
    (
        """Day 2
October 15

Victim: Hazuki
Staging: The Doll's Dance
Method: Pushed into the geyser.

Hazuki was drugged and taken out in his wheelchair. The Valkyries played at 9. To make us think he was in his room?""",
        """Time of crime

Hazuki went to his room after dinner, about 8. Only Manami went in or out.

Likely taken out between 8:30 and 9, when the music played.

Body found at 10, at the geyser.""",
    ),
    (
        """Mayura: Bath with Miyuki and Mimura, 8:30 to 9. At the geyser at 10.
Yone: Clearing up in the kitchen and dining room.
Manami: In Hazuki's room. Says she was drugged.
Mimura: Bath with Miyuki and Mayura, 8:30 to 9. Then her room.""",
        """Endo: In his room.
Tomyo: Asleep in the great hall.
Sayuri: In her room. Went to Hazuki's room to retrieve her watch before the music played.""",
    ),
    (
        """Day 3
October 16

Victim: Fake Masaki (Osamu Sato)
Staging: Spear of the Dead
Method: Stabbed once through the heart.""",
        """Circumstances
A boat with the body was found in the cove.

At 10 a.m. the fake Masaki argued with Mimura and Endo, ran from the house and went missing.

Mayura says she found the boat before 6 p.m.""",
    ),
    (
        """Mayura: To the shrine with Miyuki and me at 11:30. Cove that evening.
Manami: Fought with Sayuri that evening. Fell off the cliff, blacked out and was taken to the infirmary.
Mimura: Says she searched for fake Masaki till after noon. Then her room. Met Itsuki about 2. Treated Manami that evening.""",
        """Endo: In his room.
Tomyo: Hunted for treasure across the island. At 5:30 p.m. took a leak at the cove.
Sayuri: In the boat with me after 10, then in her room. Fought with Manami that evening.
Yone: Says she did housework and never went outside.""",
    ),
)


def memo_edits(base, records):
    data, pages, palettes = graphics.load(PATH, records)
    if extent(base, records[PATH]) != data or not palettes or palettes[0][3] != 16:
        raise ValueError("Memo1 source or palette has changed")
    font = ImageFont.truetype("C:/Windows/Fonts/tahomabd.ttf", 10)
    edits = []
    for index, columns in enumerate(PAGES):
        offset = pages[index][0]
        mask = Image.new("1", (256, 256))
        ink = ImageDraw.Draw(mask)
        for left, right, copy in zip((2, 132), (120, 250), columns):
            y = 1
            for paragraph in copy.splitlines():
                line = ""
                for word in paragraph.split():
                    candidate = f"{line} {word}".strip()
                    if line and ink.textbbox((left, y), candidate, font=font)[2] > right:
                        ink.text((left, y), line, font=font, fill=1)
                        y += 12
                        line = word
                    else:
                        line = candidate
                bounds = ink.textbbox((left, y), line, font=font)
                if bounds[2] > right or bounds[3] > 256:
                    raise ValueError(f"Memo page {index + 1} does not fit")
                ink.text((left, y), line, font=font, fill=1)
                y += 12
        if mask.crop((120, 0, 132, 256)).getbbox():
            raise ValueError("Memo column gap contains ink")
        image = graphics.decode(data[offset:offset + 32768], 4)
        image.frombytes(mask.convert("L").tobytes().translate(bytes([0] * 255 + [2])))
        edits.append((offset, offset + 32768, graphics.encode(image, 4)))
    return data, edits
