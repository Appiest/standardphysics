"""DeepFusion: InverseAug and LearnableAlign for LiDAR-Camera Gaussian Splatting."""

from __future__ import annotations

import math
from typing import Sequence, overload

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from standardphysics_pipeline.textures.camera import PhotoCamera

from .feature_extractor import ImageFeatureExtractor


class InverseAug:
    """Reverses coordinate transformations and geometric augmentations before fusion.

    Maintains metric Z-up room frame alignment with ARKit camera frames.
    """

    @staticmethod
    @overload
    def room_to_camera_points(
        points: torch.Tensor, camera: PhotoCamera
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]: ...

    @staticmethod
    @overload
    def room_to_camera_points(
        points: np.ndarray, camera: PhotoCamera
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]: ...

    @staticmethod
    def room_to_camera_points(
        points: np.ndarray | torch.Tensor,
        camera: PhotoCamera,
    ) -> tuple[np.ndarray | torch.Tensor, np.ndarray | torch.Tensor, np.ndarray | torch.Tensor]:
        """Project room-frame points to pixel (u, v) and camera-forward depth.

        Depth <= 0 is behind the camera.
        """
        if isinstance(points, torch.Tensor):
            pts_np = points.detach().cpu().numpy()
            u, v, d = camera.project(pts_np)
            device = points.device
            return (
                torch.from_numpy(u).to(device=device, dtype=torch.float32),
                torch.from_numpy(v).to(device=device, dtype=torch.float32),
                torch.from_numpy(d).to(device=device, dtype=torch.float32),
            )
        return camera.project(points)


class LearnableAlign(nn.Module):
    """Cross-modality attention module aligning LiDAR 3D points with camera features.

    Instead of unconstrained quadratic attention over all pixels, LearnableAlign
    geometrically restricts cross-attention to the camera keyframes where each 3D
    point actually projects, learning dynamic view weights to filter occlusions and
    viewpoint angle variations.
    """

    def __init__(self, point_dim: int = 64, image_dim: int = 64, hidden_dim: int = 64):
        super().__init__()
        self.point_dim = point_dim
        self.image_dim = image_dim
        self.hidden_dim = hidden_dim

        # Projections for cross-attention
        self.q_proj = nn.Linear(point_dim, hidden_dim, bias=False)
        self.k_proj = nn.Linear(image_dim, hidden_dim, bias=False)
        self.v_proj = nn.Linear(image_dim, hidden_dim, bias=False)

        # Out projection combining point feature with aggregated camera feature
        self.out_proj = nn.Sequential(
            nn.Linear(point_dim + hidden_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.SiLU(inplace=True),
        )

        # Fallback embedding for unobserved points
        self.unobserved_embedding = nn.Parameter(torch.zeros(hidden_dim))
        nn.init.normal_(self.unobserved_embedding, std=0.02)

    def forward(
        self,
        point_features: torch.Tensor,
        points: torch.Tensor,
        cameras: Sequence[PhotoCamera],
        feature_maps: list[torch.Tensor] | torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """Align and fuse multi-view camera features into LiDAR point representations.

        Args:
            point_features: (N, point_dim) LiDAR geometric embeddings.
            points: (N, 3) 3D coordinates in room frame.
            cameras: List of M PhotoCamera objects.
            feature_maps: List of M tensors of shape (1, image_dim, H', W') or
                          batched tensor of shape (M, image_dim, H', W').

        Returns:
            fused_features: (N, hidden_dim) fused multimodal representation.
            visibility_mask: (N,) boolean tensor indicating points seen in >= 1 view.
        """
        N = points.shape[0]
        M = len(cameras)
        device = point_features.device

        if M == 0:
            # Fallback if no cameras are provided
            fused = self.out_proj(torch.cat([point_features, self.unobserved_embedding.expand(N, -1)], dim=-1))
            return fused, torch.zeros(N, dtype=torch.bool, device=device)

        # Compute query for all points: (N, hidden_dim)
        queries = self.q_proj(point_features)  # (N, D)

        # Collect sampled features and visibility across all M cameras
        all_sampled_keys = []
        all_sampled_vals = []
        visibility_masks = []

        for idx, camera in enumerate(cameras):
            fmap = feature_maps[idx] if isinstance(feature_maps, list) else feature_maps[idx:idx+1]
            u, v, depth = InverseAug.room_to_camera_points(points, camera)

            # Check inside frustum and in front of camera
            in_view = (
                (depth > 0.1)
                & (u >= 0.0)
                & (u <= camera.width - 1.0)
                & (v >= 0.0)
                & (v <= camera.height - 1.0)
            )
            visibility_masks.append(in_view)

            # Sample features at (u, v)
            # Shape of sampled: (1, N, image_dim)
            sampled = ImageFeatureExtractor.sample_features(fmap, u, v, camera.width, camera.height)
            feat_n = sampled.squeeze(0)  # (N, image_dim)

            k = self.k_proj(feat_n)  # (N, hidden_dim)
            v_val = self.v_proj(feat_n)  # (N, hidden_dim)

            all_sampled_keys.append(k)
            all_sampled_vals.append(v_val)

        # Stack over views: (N, M, hidden_dim)
        keys_stacked = torch.stack(all_sampled_keys, dim=1)
        vals_stacked = torch.stack(all_sampled_vals, dim=1)
        vis_stacked = torch.stack(visibility_masks, dim=1)  # (N, M)

        # Compute cross-attention scores: (N, M)
        # q: (N, 1, D), k: (N, M, D) -> scores: (N, M)
        scores = torch.sum(queries.unsqueeze(1) * keys_stacked, dim=-1) / math.sqrt(self.hidden_dim)

        # Mask out non-visible views with a large negative value
        scores = scores.masked_fill(~vis_stacked, -1e9)

        # Softmax over visible views
        attn_weights = F.softmax(scores, dim=-1)  # (N, M)
        # In case a point has 0 visible views, attn_weights might be NaN; sanitize
        attn_weights = torch.nan_to_num(attn_weights, nan=0.0)

        # Aggregate values: (N, hidden_dim)
        aggregated_cam = torch.sum(attn_weights.unsqueeze(-1) * vals_stacked, dim=1)

        # For points with 0 visibility, replace with unobserved_embedding
        has_visibility = vis_stacked.any(dim=1)
        aggregated_cam = torch.where(has_visibility.unsqueeze(-1), aggregated_cam, self.unobserved_embedding.expand(N, -1))

        # Concatenate and project: (N, hidden_dim)
        combined = torch.cat([point_features, aggregated_cam], dim=-1)
        fused = self.out_proj(combined)

        return fused, has_visibility
