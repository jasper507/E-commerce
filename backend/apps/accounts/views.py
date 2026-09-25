from django.contrib.auth import authenticate
from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from django.views.csrf import csrf_failure as django_csrf_failure
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.address_services import (
    AddressRuleError,
    create_address,
    delete_address,
    set_default_address,
    update_address,
)
from apps.accounts.models import Address
from apps.accounts.serializers import (
    AddressSerializer,
    AddressWriteSerializer,
    LoginSerializer,
    RegistrationSerializer,
    SessionUserSerializer,
)


@method_decorator(csrf_protect, name="dispatch")
class RegisterView(generics.CreateAPIView):
    serializer_class = RegistrationSerializer
    permission_classes = (AllowAny,)


class CsrfTokenView(APIView):
    permission_classes = (AllowAny,)

    def get(self, request):
        # 前端先获取 CSRF Cookie，后续 Session 写请求才能通过 CSRF 检查。
        get_token(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


@method_decorator(csrf_protect, name="dispatch")
class LoginView(APIView):
    permission_classes = (AllowAny,)

    def post(self, request):
        # authenticate 验证密码哈希；django_login 建立 Session，不给前端发长期 Token。
        serializer = LoginSerializer(
            data=request.data,
        )
        serializer.is_valid(raise_exception=True)
        user = authenticate(
            request=request,
            username=serializer.validated_data["username"],
            password=serializer.validated_data["password"],
        )
        if user is None:
            return Response(
                {"detail": "Invalid username or password."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        django_login(request, user)

        return Response(SessionUserSerializer(user).data)


class CurrentUserView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        return Response(SessionUserSerializer(request.user).data)


@method_decorator(csrf_protect, name="dispatch")
class LogoutView(APIView):
    permission_classes = (AllowAny,)

    def post(self, request):
        django_logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


def address_rule_error_response(error):
    """归属失败统一映射为 404，避免暴露其他用户地址是否存在。"""

    response_status = (
        status.HTTP_404_NOT_FOUND
        if error.code == "address_not_found"
        else status.HTTP_400_BAD_REQUEST
    )
    return Response(
        {"code": error.code, "detail": error.detail},
        status=response_status,
    )


class AddressCollectionView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        # 所有个人数据查询均在服务端限定当前用户，前端隐藏按钮不是权限边界。
        addresses = Address.objects.filter(user=request.user)
        return Response(AddressSerializer(addresses, many=True).data)

    def post(self, request):
        serializer = AddressWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        address = create_address(
            user=request.user,
            **serializer.validated_data,
        )
        return Response(
            AddressSerializer(address).data,
            status=status.HTTP_201_CREATED,
        )


class AddressDetailView(APIView):
    permission_classes = (IsAuthenticated,)

    def patch(self, request, address_id):
        serializer = AddressWriteSerializer(
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        try:
            address = update_address(
                user=request.user,
                address_id=address_id,
                **serializer.validated_data,
            )
        except AddressRuleError as error:
            return address_rule_error_response(error)
        return Response(AddressSerializer(address).data)

    def delete(self, request, address_id):
        try:
            delete_address(
                user=request.user,
                address_id=address_id,
            )
        except AddressRuleError as error:
            return address_rule_error_response(error)
        return Response(status=status.HTTP_204_NO_CONTENT)


class AddressSetDefaultView(APIView):
    permission_classes = (IsAuthenticated,)

    def post(self, request, address_id):
        try:
            address = set_default_address(
                user=request.user,
                address_id=address_id,
            )
        except AddressRuleError as error:
            return address_rule_error_response(error)
        return Response(AddressSerializer(address).data)


def csrf_failure(request, reason=""):
    if not request.path.startswith("/api/"):
        return django_csrf_failure(request, reason=reason)

    return JsonResponse(
        {"detail": "CSRF verification failed."},
        status=status.HTTP_403_FORBIDDEN,
    )
