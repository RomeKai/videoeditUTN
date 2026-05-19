from rest_framework.test import APITestCase
from rest_framework import status
from django.urls import reverse
from django.contrib.auth import get_user_model
from apps.videos.models import VideoProject, BrandKit

User = get_user_model()

# Usaremos la ruta que definimos: /api/v1/projects/
PROJECTS_URL = reverse('project-list')
BRANDKITS_URL = reverse('brandkit-list')

class PublicVideoProjectAPITests(APITestCase):
    """Pruebas de API para proyectos de video que NO requieren autenticación"""

    def test_auth_required(self):
        """Asegura que se requiere autenticación para acceder a la lista de proyectos."""
        res = self.client.get(PROJECTS_URL)
        # Debe fallar con 401 (No autorizado) si no hay token
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)


class PrivateVideoProjectAPITests(APITestCase):
    """Pruebas de API para proyectos de video que SÍ requieren autenticación"""

    def setUp(self):
        from apps.users.models import Workspace
        # 1. Crear usuario de prueba para autenticar
        self.user = User.objects.create_user(
            username='testuser',
            email='test@onecreator.com',
            password='testpassword'
        )
        # 2. Autenticar el cliente de prueba con el usuario
        self.client.force_authenticate(user=self.user)
        
        # 3. Crear Workspace
        self.workspace = Workspace.objects.create(name="Test Workspace", owner=self.user)
        self.workspace.members.add(self.user)

        # 4. Crear proyectos de prueba
        self.project1 = VideoProject.objects.create(
            workspace=self.workspace,
            uploaded_by=self.user,
            title='Mi Primer Clip',
            source_file='test/path/file1.mp4'
        )
        
        # Crear un proyecto que pertenece a OTRO usuario/workspace
        other_user = User.objects.create_user(
            username='otheruser',
            email='other@onecreator.com',
            password='otherpassword'
        )
        self.other_workspace = Workspace.objects.create(name="Other Workspace", owner=other_user)
        self.other_workspace.members.add(other_user)
        
        self.project_other = VideoProject.objects.create(
            workspace=self.other_workspace,
            uploaded_by=other_user,
            title='Proyecto Secreto',
            source_file='test/path/file_secret.mp4'
        )

    def test_retrieve_projects_list_success(self):
        """Asegura que se puede obtener la lista de proyectos propios."""
        res = self.client.get(PROJECTS_URL)

        self.assertEqual(len(res.data), 1) 

        self.assertEqual(res.data[0]['title'], 'Mi Primer Clip')

        self.assertNotIn(self.project_other.title, [item['title'] for item in res.data])
        
    def test_projects_limited_to_user(self):
        """Asegura que el usuario NO puede ver los proyectos de otros."""
        res = self.client.get(PROJECTS_URL)
        
        # 1. El proyecto secreto NO debe aparecer en la lista
        # CÓDIGO CORREGIDO (Línea 70 aproximada):
        # Eliminamos ['results'] porque ahora res.data es la lista directa
        self.assertNotIn(self.project_other.title, [item['title'] for item in res.data])

        # Continúa en apps/videos/tests/test_api.py

class VideoProjectUpdateTests(PrivateVideoProjectAPITests):
    """Pruebas para actualizar proyectos de video (PATCH)"""

    def test_update_project_success(self):
        """Asegura que el usuario puede actualizar el título de su proyecto."""
        # Ruta específica para el detalle del proyecto
        url = reverse('project-detail', args=[self.project1.id])
        payload = {'title': 'Nuevo Título Editado'}
        
        # Enviamos la actualización parcial (PATCH)
        res = self.client.patch(url, payload)
        
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        # Recargamos el objeto de la DB y verificamos el cambio
        self.project1.refresh_from_db()
        self.assertEqual(self.project1.title, 'Nuevo Título Editado')

    def test_cannot_update_other_user_project(self):
        """Asegura que el usuario NO puede actualizar un proyecto que no es suyo."""
        # Usamos el proyecto del 'other_user'
        url = reverse('project-detail', args=[self.project_other.id])
        payload = {'title': 'Título Hackeado'}
        
        res = self.client.patch(url, payload)
        
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        # Verificamos que el título original NO cambió
        self.project_other.refresh_from_db()
        self.assertNotEqual(self.project_other.title, 'Título Hackeado')

    def test_cannot_update_read_only_fields(self):
        """Asegura que no se puede modificar el estado (status)."""
        url = reverse('project-detail', args=[self.project1.id])
        # Intentamos actualizar un campo de solo lectura
        payload = {'status': 'failed'} 
        
        res = self.client.patch(url, payload)
        
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        # El estado real en la DB debe seguir siendo el valor por defecto
        self.project1.refresh_from_db()
        self.assertEqual(self.project1.status, VideoProject.Status.UPLOADED)


class VideoProjectDeleteTests(PrivateVideoProjectAPITests):
    """Pruebas para eliminar proyectos de video (DELETE)"""
    
    def test_delete_project_success(self):
        """Asegura que el usuario puede eliminar su propio proyecto."""
        url = reverse('project-detail', args=[self.project1.id])
        # Cuenta cuántos proyectos hay antes de eliminar
        count_before = VideoProject.objects.count()
        
        res = self.client.delete(url)
        
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        # Verificamos que se redujo el contador
        self.assertEqual(VideoProject.objects.count(), count_before - 1)
        
    def test_cannot_delete_other_user_project(self):
        """Asegura que el usuario NO puede eliminar un proyecto de otro usuario."""
        url = reverse('project-detail', args=[self.project_other.id])
        
        res = self.client.delete(url)
        
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        # Verificamos que el proyecto del otro usuario aún existe
        self.assertTrue(VideoProject.objects.filter(id=self.project_other.id).exists())