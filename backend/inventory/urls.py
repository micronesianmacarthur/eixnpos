from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    StockMovementViewSet,
    RepackCreateView,
    RepackDetailView,
    RepackListView,
)

router = DefaultRouter()
router.register(r'', StockMovementViewSet, basename='movement')

# IMPORTANT: this router serves the StockMovementViewSet at the catch-all prefix
# ``r''``, whose detail route is ``^(?P<pk>[^/.]+)/$`. A second router prefix such
# as ``r'repacks'`` registered after it would be shadowed (``/api/inventory/repacks/``
# would match the ``r''`` detail route with pk='repacks'). So the repack endpoints
# are registered as explicit ``path()`` entries AHEAD of the router include; do not
# add another DefaultRouter prefix and do not rely on registration order.
# ``repacks/<int:pk>/`` is deliberately a no-mutation route (405 for every verb)
# so PUT/PATCH/DELETE on an individual repack return 405 as the plan requires.
urlpatterns = [
    path('repack/', RepackCreateView.as_view(), name='repack-create'),
    path('repacks/', RepackListView.as_view(), name='repack-list'),
    path('repacks/<int:pk>/', RepackDetailView.as_view(), name='repack-detail'),
    path('', include(router.urls)),
]
