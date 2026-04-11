"""Legal disclaimers, attribution, and compliance constants."""

DISCLAIMER = (
    "This tool is not affiliated with or endorsed by Wizards of the Coast. "
    "Magic: The Gathering and its logos are trademarks of Wizards of the Coast LLC. "
    "Card data provided by Scryfall (https://scryfall.com)."
)

ATTRIBUTION = "Card data provided by Scryfall — https://scryfall.com"

SCRYFALL_IMAGE_BASE = "https://cards.scryfall.io"


def scryfall_image_url(scryfall_id: str, face: str = "front", size: str = "normal") -> str:
    """Build a Scryfall hotlink URL for a card image. Never host images locally.

    Args:
        scryfall_id: The Scryfall UUID for the card.
        face: 'front' or 'back' for DFCs.
        size: 'small', 'normal', 'large', 'png', 'art_crop', 'border_crop'.
    """
    id_prefix = scryfall_id[:2]
    return f"{SCRYFALL_IMAGE_BASE}/{size}/{face}/{id_prefix}/{scryfall_id}.jpg"
