"""
utils.py – Stage-level helper utilities.
"""

import omni.usd
from pxr import UsdGeom, Sdf, Gf, UsdPhysics, PhysxSchema


class Utils:

    @staticmethod
    def create_mesh(stage, path, points, normals, indices, vertex_counts):
        mesh = UsdGeom.Mesh.Define(stage, path)
        mesh.CreateFaceVertexCountsAttr().Set(vertex_counts)
        mesh.CreateFaceVertexIndicesAttr().Set(indices)
        mesh.CreatePointsAttr().Set(points)
        mesh.CreateDoubleSidedAttr().Set(False)
        mesh.CreateNormalsAttr().Set(normals)
        return mesh

    @staticmethod
    def create_mesh_square_axis(stage, path, axis, half_size):
        if axis == "X":
            points = [
                Gf.Vec3f(0.0, -half_size, -half_size),
                Gf.Vec3f(0.0,  half_size, -half_size),
                Gf.Vec3f(0.0,  half_size,  half_size),
                Gf.Vec3f(0.0, -half_size,  half_size),
            ]
            normals = [Gf.Vec3f(1, 0, 0)] * 4
        elif axis == "Y":
            points = [
                Gf.Vec3f(-half_size, 0.0, -half_size),
                Gf.Vec3f( half_size, 0.0, -half_size),
                Gf.Vec3f( half_size, 0.0,  half_size),
                Gf.Vec3f(-half_size, 0.0,  half_size),
            ]
            normals = [Gf.Vec3f(0, 1, 0)] * 4
        else:  # Z
            points = [
                Gf.Vec3f(-half_size, -half_size, 0.0),
                Gf.Vec3f( half_size, -half_size, 0.0),
                Gf.Vec3f( half_size,  half_size, 0.0),
                Gf.Vec3f(-half_size,  half_size, 0.0),
            ]
            normals = [Gf.Vec3f(0, 0, 1)] * 4

        indices = [0, 1, 2, 3]
        vertex_counts = [4]
        mesh = Utils.create_mesh(stage, path, points, normals, indices, vertex_counts)

        if axis not in ("X", "Y"):
            tex_coords = mesh.CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.varying)
            tex_coords.Set([(0, 0), (1, 0), (1, 1), (0, 1)])

        return mesh

    @staticmethod
    def add_ground_plane(
        stage,
        plane_path,
        axis,
        size=3000.0,
        position=Gf.Vec3f(0.0),
        color=Gf.Vec3f(0.2, 0.25, 0.25),
    ):
        """Create a visual + collision ground plane prim."""
        plane_path = omni.usd.get_stage_next_free_path(stage, plane_path, True)

        xform = UsdGeom.Xform.Define(stage, plane_path)
        xform.AddTranslateOp().Set(position)
        xform.AddOrientOp().Set(Gf.Quatf(1.0))
        xform.AddScaleOp().Set(Gf.Vec3f(1.0))

        geom_path = plane_path + "/CollisionMesh"
        entity_plane = Utils.create_mesh_square_axis(stage, geom_path, axis, size)
        entity_plane.CreateDisplayColorAttr().Set([color])

        col_path = plane_path + "/CollisionPlane"
        plane_geom = PhysxSchema.Plane.Define(stage, col_path)
        plane_geom.CreatePurposeAttr().Set("guide")
        plane_geom.CreateAxisAttr().Set(axis)

        col_prim = stage.GetPrimAtPath(col_path)
        UsdPhysics.CollisionAPI.Apply(col_prim)

        return plane_path
