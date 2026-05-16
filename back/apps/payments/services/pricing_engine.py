import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Any
from django.core.exceptions import ValidationError

logger = logging.getLogger(__name__)

class PlanLimitExceededError(ValidationError):
    """Exception raised when a project exceeds the current plan limits."""
    pass

class PricingEngine:
    """
    FinOps V2: Dynamic pricing engine for video rendering.
    Calculates costs based on duration, resolution, AI features, and user tier.
    """

    # Multipliers by Resolution
    RESOLUTION_MULTIPLIERS = {
        '720p': Decimal('0.5'),
        '1080p': Decimal('1.0'),
        '4K': Decimal('2.5'),
    }

    # AI Subtitles fixed cost (in Coins)
    AI_SUBTITLES_BASE_COST = Decimal('2.0')

    @classmethod
    def calculate_render_cost(
        cls, 
        duration_seconds: int, 
        uses_ai_subtitles: bool, 
        resolution: str, 
        plan: Any = None 
    ) -> Decimal:
        """
        Calculates the final cost in Coins for a video render.
        
        Formula: ((duration * res_mult) + ia_cost) * (1 - plan_discount)
        """
        if duration_seconds < 0:
            raise ValueError("Duration cannot be negative.")

        # 1. Validate Plan Limits
        if plan:
            if duration_seconds > plan.max_video_duration_seconds:
                raise PlanLimitExceededError(
                    f"Video duration ({duration_seconds}s) exceeds your plan limit ({plan.max_video_duration_seconds}s)."
                )
            
            # Check resolution limit (simple string comparison for now)
            # Future: implement proper hierarchy 4K > 1080p > 720p
            if resolution == '4K' and plan.max_resolution != '4K':
                raise PlanLimitExceededError("Your plan does not support 4K rendering.")

        # 2. Base Cost Calculation
        multiplier = cls.RESOLUTION_MULTIPLIERS.get(resolution, Decimal('1.0'))
        base_cost = Decimal(duration_seconds) * multiplier

        # 3. AI Features Cost
        ai_cost = cls.AI_SUBTITLES_BASE_COST if uses_ai_subtitles else Decimal('0.0')

        # 4. Apply Plan Discount
        discount = plan.base_render_discount if plan else Decimal('0.0')
        
        total_cost = (base_cost + ai_cost) * (Decimal('1.0') - discount)

        # 5. Security & Precision
        # Never allow zero or negative cost (minimum 0.01 coins)
        total_cost = max(Decimal('0.01'), total_cost)

        # Round to 2 decimal places
        return total_cost.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
