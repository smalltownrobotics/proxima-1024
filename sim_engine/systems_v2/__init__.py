"""V2 systems — implementing the framework System contract."""
from .biology import BiologySystem
from .ethos import EthosSystem
from .events import EmergentEventsSystem
from .factions import FactionsSystem
from .genetics import GeneticsSystem
from .governance import GovernanceSystem
from .modifiers import FertilityModifierSystem, MortalityModifierSystem
from .morale_stub import MoraleStub
from .pathogens import PathogenSystem
from .population import PopulationSystem
from .resources import ResourcesSystem
from .ship_integrity import ShipIntegritySystem
from .tension import TensionSystem

__all__ = [
    "BiologySystem", "EmergentEventsSystem", "EthosSystem", "FactionsSystem",
    "FertilityModifierSystem", "GeneticsSystem", "GovernanceSystem",
    "MoraleStub", "MortalityModifierSystem", "PathogenSystem",
    "PopulationSystem", "ResourcesSystem", "ShipIntegritySystem",
    "TensionSystem",
]
