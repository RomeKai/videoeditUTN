import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Any, Dict
from django.core.exceptions import ValidationError

logger = logging.getLogger(__name__)

class PlanLimitExceededError(ValidationError):
    """Exception raised when a project exceeds the current plan limits (duration, resolution)."""
    pass

class FeatureNotAllowedError(ValidationError):
    """Exception raised when a requested premium feature is not allowed by the user's plan."""
    pass

class PricingEngine:
    """
    FinOps V3: Advanced dynamic pricing engine for video SaaS.
    Calculates costs based on Unit Economics, Resolution Multipliers, and Feature Add-ons.
    """

    # --- Unit Economics (Rules of Gold) ---
    BASE_COST_PER_SECOND_720P = Decimal('0.5')
    
    RESOLUTION_MULTIPLIERS = {
        '720p': Decimal('1.0'), # Base
        '1080p': Decimal('1.5'),
        '4K': Decimal('3.0'),
    }

    # --- Feature Add-on Costs (Fixed) ---
    FEATURE_COSTS = {
        'seo_optimization': Decimal('5.0'),
        'ai_thumbnail': Decimal('15.0'),
    }

    @classmethod
    def calculate_render_cost(
        cls, 
        duration_seconds: int, 
        resolution: str, 
        features_requested: Dict[str, bool], 
        plan: Any = None 
    ) -> Decimal:
        """
        Calculates the final cost in Coins for a video render (FinOps V3).
        
        Formula: 
        1. Base = duration * 0.5 * res_multiplier
        2. Features = sum(requested feature costs)
        3. Total = (Base + Features) * (1 - base_discount_rate)
        """
        if duration_seconds < 0:
            raise ValueError("Duration cannot be negative.")

        # 1. VALIDATION: Tier Constraints
        if plan:
            # A. Duration Check
            if duration_seconds > plan.max_video_duration_seconds:
                raise PlanLimitExceededError(
                    f"Video duration ({duration_seconds}s) exceeds your plan limit ({plan.max_video_duration_seconds}s)."
                )
            
            # B. Resolution Check (Hierarchy: 4K > 1080p > 720p)
            res_hierarchy = {'720p': 1, '1080p': 2, '4K': 3}
            requested_rank = res_hierarchy.get(resolution, 1)
            allowed_rank = res_hierarchy.get(plan.max_resolution, 1)
            
            if requested_rank > allowed_rank:
                raise PlanLimitExceededError(f"Your plan '{plan.name}' does not support {resolution} rendering.")

            # C. Feature Check
            if features_requested.get('seo_optimization') and not plan.has_seo_optimization:
                raise FeatureNotAllowedError("SEO Optimization is not allowed in your current plan.")
            
            if features_requested.get('ai_thumbnail') and not plan.has_thumbnail_engine:
                raise FeatureNotAllowedError("IA Thumbnail Engine is not allowed in your current plan.")

        # 2. MATH: Base Render Cost
        res_multiplier = cls.RESOLUTION_MULTIPLIERS.get(resolution, Decimal('1.0'))
        base_render_cost = Decimal(duration_seconds) * cls.BASE_COST_PER_SECOND_720P * res_multiplier

        # 3. MATH: Feature Add-ons
        add_on_cost = Decimal('0.0')
        for feature, requested in features_requested.items():
            if requested:
                add_on_cost += cls.FEATURE_COSTS.get(feature, Decimal('0.0'))

        # 4. MATH: Apply Plan Discount
        discount_rate = plan.base_discount_rate if plan else Decimal('0.0')
        
        total_cost = (base_render_cost + add_on_cost) * (Decimal('1.0') - discount_rate)

        # 5. SECURITY: Guarantee positivity
        total_cost = max(Decimal('0.01'), total_cost)

        # Round to 2 decimal places for financial integrity
        return total_cost.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
