from rest_framework import generics
from rest_framework.permissions import AllowAny

from apps.accounts.serializers import RegistrationSerializer


class RegisterView(generics.CreateAPIView):
    serializer_class = RegistrationSerializer
    permission_classes = (AllowAny,)
