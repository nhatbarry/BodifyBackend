from app.schemas.auth import (
    AuthUser,
    LoginRequest,
    MeResponse,
    RefreshRequest,
    RegisterRequest,
    RegisterResponse,
    TokenResponse,
)
from app.schemas.body_metrics import (
    BodyMetricBase,
    BodyMetricCreate,
    BodyMetricRead,
    BodyMetricUpdate,
)
from app.schemas.equipment import (
    EquipmentBase,
    EquipmentCreate,
    EquipmentRead,
    EquipmentUpdate,
)
from app.schemas.exercise_equipment import (
    ExerciseEquipmentCreate,
    ExerciseEquipmentRead,
)
from app.schemas.exercise_media import (
    ExerciseMediaBase,
    ExerciseMediaCreate,
    ExerciseMediaRead,
    ExerciseMediaUpdate,
)
from app.schemas.exercise_muscles import (
    ExerciseMuscleBase,
    ExerciseMuscleCreate,
    ExerciseMuscleRead,
    ExerciseMuscleUpdate,
)
from app.schemas.exercise_translations import (
    ExerciseTranslationBase,
    ExerciseTranslationCreate,
    ExerciseTranslationRead,
    ExerciseTranslationUpdate,
)
from app.schemas.exercises import (
    ExerciseBase,
    ExerciseCreate,
    ExerciseRead,
    ExerciseUpdate,
)
from app.schemas.muscle_groups import (
    MuscleGroupBase,
    MuscleGroupCreate,
    MuscleGroupRead,
    MuscleGroupUpdate,
)
from app.schemas.user_profiles import (
    UserProfileBase,
    UserProfileCreate,
    UserProfileRead,
    UserProfileUpdate,
)
from app.schemas.users import UserRead

__all__ = [
    "AuthUser",
    "LoginRequest",
    "MeResponse",
    "RefreshRequest",
    "RegisterRequest",
    "RegisterResponse",
    "TokenResponse",
    "BodyMetricBase",
    "BodyMetricCreate",
    "BodyMetricRead",
    "BodyMetricUpdate",
    "EquipmentBase",
    "EquipmentCreate",
    "EquipmentRead",
    "EquipmentUpdate",
    "ExerciseBase",
    "ExerciseCreate",
    "ExerciseRead",
    "ExerciseUpdate",
    "ExerciseEquipmentCreate",
    "ExerciseEquipmentRead",
    "ExerciseMediaBase",
    "ExerciseMediaCreate",
    "ExerciseMediaRead",
    "ExerciseMediaUpdate",
    "ExerciseMuscleBase",
    "ExerciseMuscleCreate",
    "ExerciseMuscleRead",
    "ExerciseMuscleUpdate",
    "ExerciseTranslationBase",
    "ExerciseTranslationCreate",
    "ExerciseTranslationRead",
    "ExerciseTranslationUpdate",
    "MuscleGroupBase",
    "MuscleGroupCreate",
    "MuscleGroupRead",
    "MuscleGroupUpdate",
    "UserRead",
    "UserProfileBase",
    "UserProfileCreate",
    "UserProfileRead",
    "UserProfileUpdate",
]
